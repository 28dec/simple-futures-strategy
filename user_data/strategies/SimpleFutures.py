# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
# --- Do not remove these libs ---
import math
import numpy as np  # noqa
import pandas as pd  # noqa
pd.options.mode.chained_assignment = None
from pandas import DataFrame
from typing import Optional, Union
from datetime import datetime
from freqtrade.persistence import Trade
import logging

from freqtrade.strategy import (BooleanParameter, CategoricalParameter, DecimalParameter,
                                IStrategy, IntParameter, stoploss_from_open)

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
logger = logging.getLogger(__name__)

def smi_trend(df: DataFrame, k_length=9, d_length=3, smoothing_type='EMA', smoothing=10):
    ll = df['low'].rolling(window=k_length).min()
    hh = df['high'].rolling(window=k_length).max()

    diff = hh - ll
    rdiff = df['close'] - (hh + ll) / 2

    avgrel = rdiff.ewm(span=d_length).mean().ewm(span=d_length).mean()
    avgdiff = diff.ewm(span=d_length).mean().ewm(span=d_length).mean()

    smi = np.where(avgdiff != 0, (avgrel / (avgdiff / 2) * 100), 0)

    if smoothing_type == 'SMA':
        smi_ma = ta.SMA(smi, timeperiod=smoothing)
    elif smoothing_type == 'EMA':
        smi_ma = ta.EMA(smi, timeperiod=smoothing)
    elif smoothing_type == 'WMA':
        smi_ma = ta.WMA(smi, timeperiod=smoothing)
    elif smoothing_type == 'DEMA':
        smi_ma = ta.DEMA(smi, timeperiod=smoothing)
    elif smoothing_type == 'TEMA':
        smi_ma = ta.TEMA(smi, timeperiod=smoothing)
    else:
        raise ValueError("Choose an MA Type: 'SMA', 'EMA', 'WMA', 'DEMA', 'TEMA'")

    conditions = [
        (np.greater(smi, 0) & np.greater(smi, smi_ma)), # (2) Bull 
        (np.less(smi, 0) & np.greater(smi, smi_ma)),    # (1) Possible Bullish Reversal
        (np.greater(smi, 0) & np.less(smi, smi_ma)),    # (-1) Possible Bearish Reversal
        (np.less(smi, 0) & np.less(smi, smi_ma))        # (-2) Bear
    ]

    smi_trend = np.select(conditions, [2, 1, -1, -2])

    return smi, smi_ma, smi_trend

def detect_pullback(df: DataFrame, periods=30, method='candle_body'):
    if method == 'stdev_outlier':
        outlier_threshold = 2.0
        df['dif'] = df['close'] - df['close'].shift(1)
        df['dif_squared_sum'] = (df['dif']**2).rolling(window=periods + 1).sum()
        df['std'] = np.sqrt((df['dif_squared_sum'] - df['dif'].shift(0)**2) / (periods - 1))
        df['z'] = df['dif'] / df['std']
        df['pullback_flag'] = np.where(df['z'] >= outlier_threshold, 1, 0)
        df['pullback_flag'] = np.where(df['z'] <= -outlier_threshold, -1, df['pullback_flag'])

    if method == 'pct_outlier':
        outlier_threshold = 2.0
        df["pb_pct_change"] = df["close"].pct_change()
        df['pb_zscore'] = qtpylib.zscore(df, window=periods, col='pb_pct_change')
        df['pullback_flag'] = np.where(df['pb_zscore'] >= outlier_threshold, 1, 0)
        df['pullback_flag'] = np.where(df['pb_zscore'] <= -outlier_threshold, -1, df['pullback_flag'])

    if method == 'candle_body':
        pullback_pct = 1.0
        df['change'] = df['close'] - df['open']
        df['pullback'] = (df['change'] / df['open']) * 100
        df['pullback_flag'] = np.where(df['pullback'] >= pullback_pct, 1, 0)
        df['pullback_flag'] = np.where(df['pullback'] <= -pullback_pct, -1, df['pullback_flag'])

    return df

class SimpleFutures(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '1h'

    can_short = True
    use_custom_stoploss=True
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count: int = 0
    stoploss = -0.291 # custom_stoploss would no lower than this dead line
    order_types = {
        'entry': 'market',
        'exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': False
    }

#    pullback_detect_method = CategoricalParameter(['stdev_outlier', 'pct_outlier', 'candle_body'], default = 'pct_outlier', space = 'buy', optimize = True)

    # SMI params
    smi_k_length = IntParameter(1, 99, default=9, space="buy", optimize=True)
    smi_d_length = IntParameter(1, 99, default=3, space="buy", optimize=True)
    smi_smooth_length = IntParameter(1, 99, default=10, space="buy", optimize=True)
    ma_smoothy_type = CategoricalParameter(['SMA', 'EMA', 'WMA', 'DEMA', 'TEMA'], default = 'EMA', space = 'buy', optimize = True)

    # Pullback params
#    short_pullback_exit = DecimalParameter(-0.99, 0.99, default=0.5, space='sell')
#    long_pullback_exit = DecimalParameter(-0.99, 0.99, default=-0.5, space='sell')

    ## Trailing params
    # https://discordapp.com/channels/700048804539400213/852593312116375642/1053608836369502278
    # it definitely makes the trailing stop less sensitive to in-candle moves and avoids getting stopped out too early
    is_optimize_trailing = True
    pHSL = DecimalParameter(-0.200, -0.040, default=-0.08, decimals=3, space='sell', load=True, optimize=is_optimize_trailing)
    pPF_1 = DecimalParameter(0.008, 0.030, default=0.016, decimals=3, space='sell', load=True, optimize=is_optimize_trailing)
    pSL_1 = DecimalParameter(0.008, 0.030, default=0.011, decimals=3, space='sell', load=True, optimize=is_optimize_trailing)
    # profit threshold 2, SL_2 is used
    pPF_2 = DecimalParameter(0.050, 0.200, default=0.080, decimals=3, space='sell', load=True, optimize=is_optimize_trailing)
    pSL_2 = DecimalParameter(0.030, 0.200, default=0.040, decimals=3, space='sell', load=True, optimize=is_optimize_trailing)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['smi'], dataframe['smi_ma'], dataframe['smi_trend'] = smi_trend(dataframe, self.smi_k_length.value, self.smi_d_length.value, self.ma_smoothy_type.value)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (qtpylib.crossed_above(dataframe['smi_trend'], 0))
            ),
            ['enter_long', 'enter_tag']
        ] = (1, 'smi_long')
        dataframe.loc[
            (
                (qtpylib.crossed_below(dataframe['smi_trend'], 0))
            ),
            ['enter_short', 'enter_tag']
        ] = (1, 'smi_short')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str], side: str,
                 **kwargs) -> float:
        return 3.0

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:

        HSL = self.pHSL.value
        PF_1 = self.pPF_1.value
        SL_1 = self.pSL_1.value
        PF_2 = self.pPF_2.value
        SL_2 = self.pSL_2.value

        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        current_candle = dataframe.iloc[-1].squeeze()
        current_profit = trade.calc_profit_ratio(current_candle['close'])

        if current_profit > PF_2:
            sl_profit = SL_2 + (current_profit - PF_2)
        elif current_profit > PF_1:
            sl_profit = SL_1 + ((current_profit - PF_1) * (SL_2 - SL_1) / (PF_2 - PF_1))
        else:
            sl_profit = HSL

        if self.can_short:
            if (-1 + ((1 - sl_profit) / (1 - current_profit))) <= 0:
                return 1
        else:
            if (1 - ((1 + sl_profit) / (1 + current_profit))) <= 0:
                return 1

        return stoploss_from_open(sl_profit, current_profit, is_short=trade.is_short) or 1

#    def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime', current_rate: float,
#                    current_profit: float, **kwargs):
#        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
#        last_candle = dataframe.iloc[-1].squeeze()
#        dataframe = detect_pullback(dataframe, 30)
#        if "short" in trade.enter_tag and last_candle["pullback_flag"] > self.short_pullback_exit.value:
#            return "short_pullback_exit"
#        if "long" in trade.enter_tag and last_candle["pullback_flag"] < self.long_pullback_exit.value:
#            return "long_pullback_exit"
