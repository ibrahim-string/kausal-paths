import pandas as pd
import numpy as np
from params import StringParameter
from nodes.calc import extend_last_historical_value
from nodes.constants import VALUE_COLUMN, YEAR_COLUMN, FORECAST_COLUMN
from nodes.dimensions import Dimension
from nodes.actions import ActionNode


class DatasetAction(ActionNode):
    allowed_parameters = [StringParameter('gpc_sector', description = 'GPC Sector', is_customizable = False)]

    no_effect_value = 0.0

    qlookup = {'currency': 'Price',
               'emission_factor': 'Emission Factor',
               'emissions': 'Emissions',
               'energy': 'Energy Consumption',
               'fuel_consumption': 'Fuel Consumption',
               'mass': 'Waste Disposal',
               'mileage': 'Mileage',
               'unit_price': 'Unit Price'}

    def makeid(self, name: str):
        return (name.lower().replace('.', '').replace(',', '').replace(':', '').replace('-', '').replace(' ', '_')
                .replace('&', 'and').replace('å', 'a').replace('ä', 'a').replace('ö', 'o'))

    def compute_effect(self) -> pd.DataFrame:
        sector = self.get_parameter_value('gpc_sector')

        df = self.get_input_dataset()
        df = df[(df.index.get_level_values('Sector') == sector) &
                (df.index.get_level_values('Quantity') == self.qlookup[self.quantity])]

        droplist = ['Sector', 'Quantity']
        for i in df.index.names:
            empty = df.index.get_level_values(i).all()
            if empty is np.nan or empty == '.':
                droplist.append(i)

        df.index = df.index.droplevel(droplist)

        unit = df['Unit'].unique()[0]
        df['Value'] = df['Value'].astype('pint[' + unit + ']')
        df = df.drop(columns = ['Unit'])

        dims = []
        for i in list(df.index.names):
            if i == YEAR_COLUMN:
                dims.append(i)
            else:
                dims.append(self.makeid(i))
        df.index = df.index.set_names(dims)
        df = df.reset_index()
        for i in list(set(dims) - {YEAR_COLUMN}):
            if isinstance(df[i][0], str):
                for j in range(len(df)):
                    df[i][j] = self.makeid(df[i][j])
        df = df.set_index(dims)
        df[FORECAST_COLUMN] = False
        df = df[[FORECAST_COLUMN, VALUE_COLUMN]]

        if not self.is_enabled():  # FIXME DataFrame is calculated correctly but still does not affect the model outcome?!?
            df[VALUE_COLUMN] *= self.no_effect_value

#       df = extend_last_historical_value(df, self.get_end_year())

        return df

