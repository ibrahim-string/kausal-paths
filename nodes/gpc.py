import pandas as pd
import polars as pl
import numpy as np
from params import StringParameter, BoolParameter
from nodes.calc import extend_last_historical_value
from nodes.constants import VALUE_COLUMN, YEAR_COLUMN, FORECAST_COLUMN
from nodes.dimensions import Dimension
from nodes.node import Node
from nodes.simple import AdditiveNode
from common import polars as ppl


class DatasetNode(AdditiveNode):
    allowed_parameters = [StringParameter('gpc_sector', description = 'GPC Sector', is_customizable = False)]

    qlookup = {'currency': 'Price',
               'emission_factor': 'Emission Factor',
               'emissions': 'Emissions',
               'energy': 'Energy Consumption',
               'fuel_consumption': 'Fuel Consumption',
               'mass': 'Waste Disposal',
               'mileage': 'Mileage',
               'unit_price': 'Unit Price'}

    # -----------------------------------------------------------------------------------
    def makeid(self, label: str):
        # Supported languages: Czech, Danish, English, Finnish, German, Latvian, Polish, Swedish
        idlookup = {'': ['.', ',', ':', '-', '(', ')'],
                    '_': [' '],
                    'and': ['&'],
                    'a': ['ä', 'å', 'ą', 'á', 'ā'],
                    'c': ['ć', 'č'],
                    'd': ['ď'],
                    'e': ['ę', 'é', 'ě', 'ē'],
                    'g': ['ģ'],
                    'i': ['í', 'ī'],
                    'k': ['ķ'],
                    'l': ['ł', 'ļ'],
                    'n': ['ń', 'ň', 'ņ'],
                    'o': ['ö', 'ø', 'ó'],
                    'r': ['ř'],
                    's': ['ś', 'š'],
                    't': ['ť'],
                    'u': ['ü', 'ú', 'ů', 'ū'],
                    'y': ['ý'],
                    'z': ['ź', 'ż', 'ž'],
                    'ae': ['æ'],
                    'ss': ['ß']}

        idtext = label.lower()
        if idtext[:5] == 'scope':
            idtext = idtext.replace(' ', '')

        for tochar in idlookup:
            for fromchar in idlookup[tochar]:
                idtext = idtext.replace(fromchar, tochar)

        return idtext

    # -----------------------------------------------------------------------------------
    def compute(self) -> pd.DataFrame:
        sector = self.get_parameter_value('gpc_sector')

        df = self.get_input_dataset()
        df = df[df['Value'].notnull()]
        df = df[(df.index.get_level_values('Sector') == sector) &
                (df.index.get_level_values('Quantity') == self.qlookup[self.quantity])]

        droplist = ['Sector', 'Quantity']
        for col in df.index.names:
            vals = df.index.get_level_values(col).unique().to_list()
            if vals == ['.']:
                droplist.append(col)
        df.index = df.index.droplevel(droplist)

        unit = df['Unit'].unique()[0]
        df['Value'] = df['Value'].astype('pint[' + unit + ']')
        df = df.drop(columns = ['Unit'])

        # Convert index level names from labels to IDs.
        dims = []
        for i in df.index.names:
            if i == YEAR_COLUMN:
                dims.append(i)
            else:
                dims.append(self.makeid(i))
        df.index = df.index.set_names(dims)

        # Convert levels within each index level from labels to IDs.
        dfi = df.index.to_frame(index = False)
        for col in list(set(dims) - {YEAR_COLUMN}):
            for cat in dfi[col].unique():
                dfi[col] = dfi[col].replace(cat, self.makeid(cat))

        df.index = pd.MultiIndex.from_frame(dfi)

        if FORECAST_COLUMN not in df.columns:
            df[FORECAST_COLUMN] = False
        df = df[[FORECAST_COLUMN, VALUE_COLUMN]]

        na_nodes = self.get_input_nodes(tag='non_additive')
        input_nodes = [node for node in self.input_nodes if node not in na_nodes]


#        df = ppl.to_ppdf(df)
        df = self.add_nodes(df, input_nodes)

        if len(na_nodes) > 0:
            assert len(na_nodes) == 1 # Only one multiplier allowed
            mult = na_nodes[0].get_output(target_node=self)
 #           df = mult.paths.join_over_index(df)
            df = df.join(mult, how='outer', rsuffix='_right')  # FIXME Use PPDF, not pandas.df

            df[VALUE_COLUMN] *= df[VALUE_COLUMN + '_right']
            df = df[[FORECAST_COLUMN, VALUE_COLUMN]]

#       df = extend_last_historical_value(df, self.get_end_year())

        return df


class WeatherNode(AdditiveNode):
    allowed_parameters = AdditiveNode.allowed_parameters + [
        BoolParameter('weather_normalization', description = 'Is energy normalized for weather?')
    ]
    def compute(self):
        df = super().compute()
        is_corrected = self.get_parameter_value('weather_normalization', required=True)

        if is_corrected:
            df = df.with_columns(pl.col(VALUE_COLUMN) * pl.lit(0) + pl.lit(1))

        return df