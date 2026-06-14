# -*- coding: utf-8 -*-
"""
VCP 緊度派 gate 歷史回測（as-of 回放）
=====================================
驗證「趨勢模板(#1-#7) + VCP today_breakout」進場、跌破均線出場的歷史期望值。

設計（訊號級）：
- as-of：對每個交易日，用「截至當日」的 df 判斷趨勢模板 + VCP 突破（無 look-ahead）。
- 進場：突破日收盤價。停損：vcp.analyze 的 stop（樞紐低/末段低）。
- 出場：收盤跌破 N 日均線（10/21/50 各跑一遍比較，數據選最佳）。
- R = (exit - entry) / (entry - stop)；停損出場 ≈ -1R。勝 = R > 0。
- 每個突破訊號獨立計 R（找到訊號後跳 SIGNAL_GAP 天避免同檔密集重複）；
  不模擬資金/同時持倉上限（資金級回測是後續進階）。
- 純讀本地 offline 快取，0 API。

⚠️ RS#8（全市場百分位）未納入回測過濾（全市場每日重算成本高）。
   故結果＝「趨勢模板 + VCP 形態」的純期望值；實盤再加 RS≥80 過濾，期望值應更佳（更嚴）。

用法：
  .venv/Scripts/python.exe backtest.py                # 全市場、全可用區間
  .venv/Scripts/python.exe backtest.py --limit 100    # 先測 100 檔
  .venv/Scripts/python.exe backtest.py --start 2025-01-01
"""
from __future__ import annotations
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

# 自定位到 repo root，讓 import config / src 與相對路徑 data/ 都正確
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows console cp950 → 避免中文/減號炸
except Exception:
    pass

import config as C
from src import indicators, trend_template, vcp

PRICE_DIR = os.path.join("data", "prices")
WARMUP = 252          # 需 252 交易日才有完整 52 週高 / 200MA
SIGNAL_GAP = 10       # 找到突破訊號後跳 N 天，避免同檔連日重複計
EXIT_MAS = [10, 21, 50]
DIAG = {"days": 0, "price_ok": 0, "vcp_setup": 0, "breakout": 0}   # 診斷漏斗


def list_universe(limit=None):
    ids = [os.path.basename(p)[:-8] for p in glob.glob(os.path.join(PRICE_DIR, "*.parquet"))]
    ids = [i for i in ids if i.isdigit() and len(i) == 4]   # 只留 4 位數股票代號
    ids.sort()
    return ids[:limit] if limit else ids


def load(sid):
    df = pd.read_parquet(os.path.join(PRICE_DIR, f"{sid}.parquet"))
    return df.sort_values("date").reset_index(drop=True)


def find_signals(df, start_ts):
    """as-of 掃出進場訊號 [(t, entry, stop)]。

    正確時序：用「截至 t-1」判斷趨勢模板 + VCP tight setup + 前期樞紐高；
    當日 t「收盤突破前期樞紐高 + 量增 + 未過度延伸」才進場。
    （vcp.today_breakout 的 pivot 含當日、邏輯上幾乎不觸發，故回測自行用前期樞紐高判突破。）
    """
    n = len(df)
    if n < WARMUP + 20:
        return []
    close = df["close"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    dates = df["date"].to_numpy()
    signals = []
    t = WARMUP
    while t < n - 1:
        if start_ts is not None and pd.Timestamp(dates[t]) < start_ts:
            t += 1
            continue
        DIAG["days"] += 1
        prev = df.iloc[:t]                       # 截至 t-1（不含當日，避免 look-ahead）
        m = indicators.trend_metrics(prev)
        if not trend_template.evaluate_metrics(m)["price_template_ok"]:
            t += 1
            continue
        DIAG["price_ok"] += 1
        res = vcp.analyze(prev, dist_52w_high=m["dist_52w_high"])
        if not res.is_vcp:                       # 前一日：樞紐 tight + 近高 setup 成形
            t += 1
            continue
        DIAG["vcp_setup"] += 1
        pivot_high, stop = res.pivot_high, res.stop
        avg_vol = vol[max(0, t - C.VOLUME_AVG_WINDOW):t].mean()
        # 當日 t 收盤突破前期樞紐高 + 量 >= 1.4x均量 + 未過度延伸(< 樞紐高x1.05)
        if (pivot_high > 0 and close[t] > pivot_high
                and close[t] <= pivot_high * (1 + C.EXTENDED_ABOVE_PIVOT)
                and avg_vol > 0 and vol[t] >= C.BREAKOUT_VOLUME_MULT * avg_vol):
            DIAG["breakout"] += 1
            entry = close[t]
            if stop > 0 and entry > stop:
                signals.append((t, entry, stop))
                t += SIGNAL_GAP
                continue
        t += 1
    return signals


def simulate_exit(t, entry, stop, low, close, ma, n):
    """從 t+1 起持有：先觸停損 (-1R)，否則收盤跌破均線出場；到資料末仍持有則以末日收盤計。"""
    risk = entry - stop
    for u in range(t + 1, n):
        if low[u] <= stop:
            return (stop - entry) / risk, u - t          # ≈ -1R
        if not np.isnan(ma[u]) and close[u] < ma[u]:
            return (close[u] - entry) / risk, u - t
    return (close[-1] - entry) / risk, (n - 1) - t       # 未出場（趨勢續抱中）


def stats(Rs):
    n = len(Rs)
    if n == 0:
        return dict(n=0, win_rate=0, avg_win=0, avg_loss=0, expectancy=0, profit_factor=0, best=0, worst=0)
    wins = [r for r in Rs if r > 0]
    losses = [r for r in Rs if r <= 0]
    sw, sl = sum(wins), sum(losses)
    return dict(
        n=n,
        win_rate=len(wins) / n,
        avg_win=(sw / len(wins)) if wins else 0.0,
        avg_loss=(sl / len(losses)) if losses else 0.0,
        expectancy=sum(Rs) / n,
        profit_factor=(sw / abs(sl)) if sl != 0 else float("inf"),
        best=max(Rs),
        worst=min(Rs),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--exit-ma", type=str, default=",".join(map(str, EXIT_MAS)))
    args = ap.parse_args()

    exit_mas = [int(x) for x in args.exit_ma.split(",")]
    start_ts = pd.Timestamp(args.start) if args.start else None
    universe = list_universe(args.limit)

    results = {em: [] for em in exit_mas}
    holds = {em: [] for em in exit_mas}
    n_signals = 0
    n_stocks_traded = 0
    date_min, date_max = None, None

    for k, sid in enumerate(universe):
        try:
            df = load(sid)
        except Exception:
            continue
        if len(df) < WARMUP + 20:
            continue
        d0, d1 = df["date"].min(), df["date"].max()
        date_min = d0 if date_min is None else min(date_min, d0)
        date_max = d1 if date_max is None else max(date_max, d1)

        signals = find_signals(df, start_ts)
        if not signals:
            continue
        n_signals += len(signals)
        n_stocks_traded += 1
        low = df["low"].to_numpy(float)
        close = df["close"].to_numpy(float)
        n = len(df)
        ma_series = {em: df["close"].rolling(em, min_periods=em).mean().to_numpy(float) for em in exit_mas}
        for (t, entry, stop) in signals:
            for em in exit_mas:
                R, hold = simulate_exit(t, entry, stop, low, close, ma_series[em], n)
                results[em].append(R)
                holds[em].append(hold)
        if (k + 1) % 300 == 0:
            print(f"  …掃描 {k+1}/{len(universe)} 檔，累計 {n_signals} 訊號", flush=True)

    # ── 報告 ──
    print("\n" + "=" * 64)
    print("VCP 緊度派 gate 歷史回測結果")
    print("=" * 64)
    print(f"資料區間：{pd.Timestamp(date_min).date() if date_min is not None else '?'} → "
          f"{pd.Timestamp(date_max).date() if date_max is not None else '?'}"
          f"（暖機 {WARMUP} 日後才開始判訊號）")
    print(f"掃描股票：{len(universe)} 檔，其中 {n_stocks_traded} 檔出現過進場訊號")
    print(f"突破訊號總數：{n_signals}")
    print(f"進場=突破日收盤；停損=VCP 樞紐低/末段低；R=(出場-進場)/(進場-停損)")
    print(f"診斷漏斗：判斷日 {DIAG['days']} → 過趨勢模板 {DIAG['price_ok']} → VCP tight setup {DIAG['vcp_setup']} → 當日突破進場 {DIAG['breakout']}")
    print("-" * 64)
    print(f"{'出場規則':<12}{'交易數':>6}{'勝率':>8}{'平均賺':>8}{'平均賠':>8}{'期望值':>9}{'獲利因子':>9}{'最大賺':>8}")
    for em in exit_mas:
        s = stats(results[em])
        pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
        avg_hold = (sum(holds[em]) / len(holds[em])) if holds[em] else 0
        print(f"{'跌破'+str(em)+'MA':<12}{s['n']:>6}{s['win_rate']*100:>7.1f}%"
              f"{s['avg_win']:>+7.2f}R{s['avg_loss']:>+7.2f}R{s['expectancy']:>+8.2f}R{pf:>9}{s['best']:>+7.1f}R"
              f"   平均持有 {avg_hold:.0f} 日")
    print("=" * 64)
    print("讀法：期望值＝每筆平均賺幾 R（正＝長期賺）。獲利因子＝總賺/總賠（>1.5 算穩健）。")
    print("註：低勝率＋高期望值是 VCP 正常特性（賠就 -1R、賺抱長段）。RS≥80 過濾未納入，實盤應更佳。")


if __name__ == "__main__":
    main()
