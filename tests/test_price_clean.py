# -*- coding: utf-8 -*-
"""價格清洗：無成交列剔除、股票分割還原。"""
import numpy as np
import pandas as pd

from src import indicators
from src.price_clean import clean_prices


def _df(close, volume=1_000_000.0):
    close = np.asarray(close, float)
    n = len(close)
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.full(n, float(volume)), "turnover": np.full(n, 1e8),
    })


def test_zero_rows_dropped_and_52w_low_recovers():
    df = _df(np.linspace(100, 200, 260))
    df.loc[200, ["open", "high", "low", "close", "volume", "turnover"]] = 0.0   # 無成交列
    assert not indicators.trend_metrics(df)["dist_52w_low"] > 0                 # 髒資料：low=0 算不出來
    out = clean_prices(df)
    assert len(out) == 259 and (out["low"] > 0).all()
    assert indicators.trend_metrics(out)["dist_52w_low"] > 0


def test_split_back_adjusted():
    pre = np.linspace(6000, 7800, 250)          # 分割前
    post = np.linspace(2790, 2900, 20)          # 一拆三後（開盤較參考價 2600 高 7%）
    out = clean_prices(_df(np.concatenate([pre, post])))
    assert abs(out["close"].iloc[249] - 7800 / 3) < 1e-6        # 取整為 1/3
    assert abs(out["volume"].iloc[0] - 3_000_000.0) < 1e-3      # 量同步還原
    assert out["close"].iloc[250] == 2790                       # 事件日之後不動
    m = indicators.trend_metrics(out)
    assert m["dist_52w_high"] > -0.25                           # 不再被當成暴跌 66%


def test_normal_moves_untouched():
    close = 100 * np.cumprod(np.where(np.arange(300) % 2 == 0, 1.10, 0.90))    # 天天漲跌停
    df = _df(close)
    pd.testing.assert_frame_equal(clean_prices(df), df)


def test_ipo_honeymoon_not_adjusted():
    close = np.concatenate([[100, 100, 40], np.linspace(40, 60, 100)])         # 上市第 3 天腰斬
    df = _df(close)
    pd.testing.assert_frame_equal(clean_prices(df), df)
