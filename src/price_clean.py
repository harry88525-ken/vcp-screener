# -*- coding: utf-8 -*-
"""價格清洗（純函式，只在記憶體內做，不回寫快取）。

FinMind 免費版／Fugle candles 給的都是「未還原」股價，兩種髒資料會直接弄壞指標：

1. 無成交列（OHLC 為 0）：low=0 會讓「距 52 週低」算不出來，該檔一整年過不了趨勢模板。
2. 股票分割／面額變更：隔夜價格掉一大截（緯穎 6669 於 2026-09-02，7800 → 開盤 2790），
   均線與 52 週高全部失真，該檔被當成暴跌踢出名單，最長一年回不來。

台股單日跌幅上限 10%，所以「開盤價不到前一日收盤的 SPLIT_GAP_MAX」只可能是分割類
公司行動，據此把事件日之前的價量還原到事件日之後的口徑。

刻意不處理（沒有可靠判據，寧可不動）：減資恢復交易的向上跳空、股票股利除權
（跌幅落在 10–45% 之間，跟興櫃／恢復交易的真實波動分不開）。要補得接 FinMind 的
除權息／減資／分割參考價資料集當 ground truth。
"""
from __future__ import annotations

import pandas as pd

SPLIT_GAP_MAX = 0.55        # 開盤 ÷ 前收 ≤ 0.55（至少一拆二再跌停）才視為分割
SPLIT_SNAP_TOL = 0.105      # 開盤離參考價最多 ±10%：比例落在 1/N 的這個範圍內就取整為 1/N
IPO_HONEYMOON_ROWS = 5      # 新上市前 5 日無漲跌幅限制，不當公司行動
_PX = ["open", "high", "low", "close"]


def _split_factor(gap: float) -> float:
    """把觀察到的跳空比例取整成最接近的 1/N（N=2..20）；對不上就用原始比例。"""
    n = round(1 / gap)
    if 2 <= n <= 20 and abs(gap * n - 1) <= SPLIT_SNAP_TOL:
        return 1 / n
    return gap


def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    """回傳清洗後的新表（升序、index 重排）。輸入不被修改。"""
    if df.empty:
        return df
    out = df[(df["close"] > 0) & (df["high"] > 0) & (df["low"] > 0)].reset_index(drop=True).copy()
    if len(out) <= IPO_HONEYMOON_ROWS + 1:
        return out
    gap = out["open"] / out["close"].shift(1)
    events = gap[gap <= SPLIT_GAP_MAX]
    if events.empty:
        return out
    out[_PX + ["volume"]] = out[_PX + ["volume"]].astype(float)
    for i, g in events.items():
        if i <= IPO_HONEYMOON_ROWS:
            continue
        f = _split_factor(float(g))
        out.loc[: i - 1, _PX] = out.loc[: i - 1, _PX] * f
        out.loc[: i - 1, "volume"] = out.loc[: i - 1, "volume"] / f
    return out
