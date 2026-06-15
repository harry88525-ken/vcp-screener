# -*- coding: utf-8 -*-
"""
華邦電(2344) as-of 分類回放
==========================
用 screener.py 的分類邏輯，對 2344 逐交易日 as-of 判斷它屬 LEADER / READY / BREAKOUT / —，
輸出「系統會在哪天把它抓進清單」的時間軸。RS Rating 用全市場每日百分位（向量化）。
純讀本地快取 + 抓大盤指數。用法：set -a; . ./.env; set +a; .venv/Scripts/python.exe replay_2344.py
"""
from __future__ import annotations
import glob
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

import config as C
from src import indicators, trend_template, vcp, rs
from src.finmind_client import FinMindClient

SID = "2344"
WARMUP = 252
START, END = "2023-06-01", "2026-06-13"


def main():
    client = FinMindClient()
    idx = client.index_price(C.MARKET_INDEX_ID, START, END).sort_values("date").reset_index(drop=True)
    trade_dates = pd.DatetimeIndex(idx["date"])
    idx_close = idx["close"].to_numpy(float)

    # 全市場 close matrix，對齊大盤交易日（算 RS 百分位用）
    cols = {}
    for p in glob.glob(os.path.join("data", "prices", "*.parquet")):
        sid = os.path.basename(p)[:-8]
        if not (sid.isdigit() and len(sid) == 4):
            continue
        s = pd.read_parquet(p)[["date", "close"]].drop_duplicates("date").set_index("date")["close"]
        cols[sid] = s
    mat = pd.DataFrame(cols).reindex(trade_dates).ffill()
    mat_arr = mat.to_numpy(float)
    stock_cols = list(mat.columns)
    if SID not in stock_cols:
        print(f"❌ {SID} 不在快取"); return
    j = stock_cols.index(SID)
    lbs = list(C.RS_WEIGHTS.items())
    maxlb = max(C.RS_WEIGHTS)

    def rs_rating_at(p):
        if p < maxlb:
            return None
        raw = np.zeros(mat_arr.shape[1])
        valid = np.ones(mat_arr.shape[1], dtype=bool)
        for d, w in lbs:
            s_now, s_then = mat_arr[p], mat_arr[p - d]
            i_ret = idx_close[p] / idx_close[p - d] - 1
            with np.errstate(divide="ignore", invalid="ignore"):
                s_ret = s_now / s_then - 1
            raw = raw + w * (s_ret - i_ret)
            valid &= np.isfinite(s_ret)
        raw[~valid] = np.nan
        pct = pd.Series(raw).rank(pct=True) * 98 + 1
        v = pct.iloc[j]
        return int(round(v)) if pd.notna(v) else None

    # 華邦電本身（含 high/low/turnover，給 trend_metrics/vcp）
    dfk = pd.read_parquet(os.path.join("data", "prices", f"{SID}.parquet")).sort_values("date").reset_index(drop=True)
    pos_in_idx = {d: k for k, d in enumerate(trade_dates)}

    rows = []
    for t in range(WARMUP, len(dfk)):
        asof = dfk["date"].iloc[t]
        p = pos_in_idx.get(pd.Timestamp(asof))
        if p is None:
            continue
        sub = dfk.iloc[:t + 1]
        m = indicators.trend_metrics(sub)
        if not np.isfinite(m["close"]) or m["close"] < C.MIN_PRICE:
            continue
        price_ok = trend_template.evaluate_metrics(m)["price_template_ok"]
        near_high = pd.notna(m["dist_52w_high"]) and m["dist_52w_high"] >= C.DIST_52W_HIGH_MIN
        liquid = bool(m["liquid"])
        idx_sub = idx[idx["date"] <= asof]
        rsl = rs.rs_line_metrics(sub, idx_sub)
        vres = vcp.analyze(sub, rs_line_new_high=rsl["rs_line_new_high"],
                           dist_52w_high=m["dist_52w_high"], next_target=m["high_52w"])
        rating = rs_rating_at(p)
        rs_ok = rating is not None and rating >= C.RS_RATING_MIN
        in_core = price_ok and rs_ok and liquid and (vres.is_vcp or near_high)
        if in_core and vres.is_vcp:
            cls = "LEADER"
        elif in_core and near_high:
            cls = "READY"
        elif price_ok and vres.today_breakout and liquid:
            cls = "BREAKOUT"
        else:
            cls = "—"
        rows.append(dict(date=str(pd.Timestamp(asof).date()), cls=cls, close=round(m["close"], 1),
                         rs=rating, dist=m["dist_52w_high"], pw=vres.pivot_width,
                         price_ok=price_ok, liquid=liquid))

    # 輸出：只列「分類改變」的轉變點
    print("\n華邦電(2344) 系統分類 as-of 回放（資料到 2026-06-12）")
    print("=" * 72)
    print(f"{'日期':<12}{'分類':<9}{'收盤':>7}{'RS':>5}{'距52高':>8}{'樞紐寬':>8}")
    print("-" * 72)
    prev = None
    first_ready = first_leader = None
    n_leader = n_ready = 0
    for r in rows:
        if r["cls"] == "LEADER":
            n_leader += 1
            if first_leader is None:
                first_leader = r["date"]
        if r["cls"] == "READY":
            n_ready += 1
            if first_ready is None:
                first_ready = r["date"]
        if r["cls"] != prev:                      # 只印轉變點
            dist = f"{r['dist']:+.1%}" if pd.notna(r["dist"]) else "  n/a"
            pw = f"{r['pw']:.1%}" if r["cls"] in ("LEADER", "READY", "BREAKOUT") else ""
            print(f"{r['date']:<12}{r['cls']:<9}{r['close']:>7}{str(r['rs'] or '-'):>5}{dist:>8}{pw:>8}")
            prev = r["cls"]
    print("=" * 72)
    print(f"第一次進 READY ：{first_ready or '從未'}")
    print(f"第一次進 LEADER：{first_leader or '從未'}")
    print(f"累計天數：LEADER {n_leader} 天 / READY {n_ready} 天（回放區間 {rows[0]['date']}~{rows[-1]['date']}）")


if __name__ == "__main__":
    main()
