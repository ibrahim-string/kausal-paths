from __future__ import annotations

from typing import TypeAlias, TYPE_CHECKING, Union

from polars.datatypes import Float32, Float64
import pandas as pd
from pint_pandas import PintType

import polars as pl
import common.polars as ppl
from nodes.units import Unit
from nodes.constants import VALUE_COLUMN, YEAR_COLUMN, FORECAST_COLUMN


if TYPE_CHECKING:
    from nodes.dimensions import Dimension
    from nodes.node import NodeMetric


Dimensions: TypeAlias = dict[str, 'Dimension']
Metrics: TypeAlias = dict[str, 'NodeMetric']

DF: TypeAlias = Union[ppl.PathsDataFrame, pl.DataFrame]


@pl.api.register_dataframe_namespace('paths')
class PathsExt:
    _df: ppl.PathsDataFrame

    def __init__(self, df: DF) -> None:
        if not isinstance(df, ppl.PathsDataFrame):
            df = ppl.to_ppdf(df)
        self._df = df

    def to_pandas(self, meta: ppl.DataFrameMeta | None = None) -> pd.DataFrame:
        df = self._df.to_pandas()
        primary_keys = meta.primary_keys if meta else self._df._primary_keys
        units = meta.units if meta else self._df._units
        if primary_keys:
            df = df.set_index(primary_keys)
        for col, unit in units.items():
            pt = PintType(unit)
            df[col] = df[col].astype(pt)
        return df

    def to_wide(self, dimensions: Dimensions = {}, metrics: Metrics = {}, meta: ppl.DataFrameMeta | None = None) -> ppl.PathsDataFrame:
        """Project the DataFrame wide (dimension categories become columns) and group by year."""

        df = self._df

        dim_ids = list(dimensions.keys())
        if not dim_ids and meta is not None:
            dim_ids = list(meta.primary_keys)
            if YEAR_COLUMN in dim_ids:
                dim_ids.remove(YEAR_COLUMN)
        dim_ids = [dim for dim in dim_ids if dim in self._df.columns]

        if metrics:
            metric_cols = [m.column_id for m in metrics.values()]
        else:
            if meta:
                metric_cols = list(meta.units.keys())
            else:
                metric_cols = []
        if not metric_cols:
            metric_cols = [VALUE_COLUMN]

        # Create a column '_dims' with all the categories included
        if not dim_ids:
            return df

        df = ppl.to_ppdf(df.with_column(
            pl.concat_list([
                pl.format('{}:{}', pl.lit(dim), pl.col(dim)) for dim in dim_ids 
            ]).arr.join('/').alias('_dims')
        ))
        mdf = None
        for metric_col in metric_cols:
            tdf = df.pivot(index=[YEAR_COLUMN, FORECAST_COLUMN], columns='_dims', values=metric_col)
            cols = [col for col in tdf.columns if col not in (YEAR_COLUMN, FORECAST_COLUMN)]
            tdf = ppl.to_ppdf(tdf.rename({col: '%s@%s' % (metric_col, col) for col in cols}))
            if mdf is None:
                mdf = tdf
            else:
                tdf = tdf.drop(columns=FORECAST_COLUMN)
                mdf = mdf.join(tdf, on=YEAR_COLUMN)
        assert mdf is not None
        return ppl.PathsDataFrame._from_pydf(mdf._df)

    def to_narrow(self) -> ppl.PathsDataFrame:
        df: ppl.PathsDataFrame | pl.DataFrame = self._df
        widened_cols = [col for col in df.columns if '@' in col]
        if not len(widened_cols):
            return df  # type: ignore
        tdf = df.melt(id_vars=[YEAR_COLUMN, FORECAST_COLUMN]).with_column(
            pl.col('variable').str.split('@').alias('_tmp')
        ).with_columns([
            pl.col('_tmp').arr.first().alias('Metric'),
            pl.col('_tmp').arr.last().str.split('/').alias('_dims'),
        ])
        df = ppl.to_ppdf(tdf)
        first = df['_dims'][0]
        dim_ids = [x.split(':')[0] for x in first]
        dim_cols = [pl.col('_dims').arr.get(idx).str.split(':').arr.get(1).alias(col) for idx, col in enumerate(dim_ids)]
        df = df.with_columns(dim_cols)
        df = df.pivot(values='value', index=[YEAR_COLUMN, FORECAST_COLUMN, *dim_ids], columns='Metric')
        df = df.with_columns([pl.col(dim).cast(pl.Categorical) for dim in dim_ids])
        return ppl.to_ppdf(df)

    def make_forecast_rows(self, end_year: int) -> ppl.PathsDataFrame:
        df: DF = self._df
        y = df[YEAR_COLUMN]
        if y.n_unique() != len(y):
            raise Exception("DataFrame has duplicated years")

        if FORECAST_COLUMN not in df.columns:
            last_hist_year = y.max()
        else:
            last_hist_year = df.filter(~pl.col(FORECAST_COLUMN))[YEAR_COLUMN].max()
        assert isinstance(last_hist_year, int)
        years = pl.DataFrame(data=range(last_hist_year + 1, end_year + 1), columns=[YEAR_COLUMN])
        df = df.join(years, on=YEAR_COLUMN, how='outer').sort(YEAR_COLUMN)
        df = df.with_column(pl.when(pl.col(YEAR_COLUMN) > last_hist_year).then(pl.lit(True)).otherwise(pl.col(FORECAST_COLUMN)).alias(FORECAST_COLUMN))
        return ppl.to_ppdf(df)

    def nafill_pad(self) -> ppl.PathsDataFrame:
        """Fill N/A values by propagating the last valid observation forward.

        Requires a DF in wide format (indexed by year).
        """

        df = self._df
        y = df[YEAR_COLUMN]
        if y.n_unique() != len(y):
            raise Exception("DataFrame has duplicated years")

        df = df.fill_null(strategy='forward')
        return df


@pl.api.register_series_namespace('units')
class UnitsExt:
    unit: Unit | None

    def __init__(self, s: pl.Series):
        self._s = s
        self.unit = None

    def set(self, unit: Unit):
        assert self._s.dtype in (Float32, Float64)
        self.unit = unit
