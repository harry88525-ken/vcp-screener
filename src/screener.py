# -*- coding: utf-8 -*-
"""
選股整合器（L1 核心鏈）
=====================
universe → 逐檔(price 快取) → 趨勢/RS/VCP/流動性 → 全市場 RS 百分位 → 分類 → leaders.json

緊度派分類：
- LEADERS  : 趨勢價格門票(A-2 #1-7) + RS Rating≥80 + 流動性 + VCP gate(近高+樞紐緊)
- READY    : 趨勢門票 + RS≥80 + 流動性 + 近高，但 VCP 樞紐尚未夠緊（觀察）
- BREAKOUT : 當日突破樞紐且量增

A-6 基本面 / A-7 籌碼 / A-8 族群 為「加分欄位」，於後續接上（JSON 合約已預留鍵）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import pandas as pd

import config as C
from src import cache, indicators, rs, trend_template, vcp
from src.finmind_client import FinMindClient

# 示範用流動性權值股清單（正式版改掃全 universe）
SAMPLE = ["2330", "2317", "2454", "2382", "2308", "2303", "3711", "2891",
          "2412", "2882", "1301", "2002", "2207", "3008", "2379", "2357",
          "4938", "2395", "2603", "3017", "3037", "6669", "3231", "2376", "1216"]


def build_universe(client: FinMindClient, sample: bool) -> pd.DataFrame:
    uni = client.universe()
    uni = uni[uni["type"].isin(["twse", "tpex"])]
    uni = uni[~uni["industry_category"].isin(C.EXCLUDE_INDUSTRIES)]
    if sample:
        uni = uni[uni["stock_id"].isin(SAMPLE)]
    return uni.reset_index(drop=True)


def analyze_stock(client, sid, name, industry, start, end, index_df) -> dict | None:
    df = cache.get_price(client, sid, start, end)
    if df.empty or len(df) < C.MA_SLOW + C.MA200_RISING_LOOKBACK:
        return None
    m = indicators.trend_metrics(df)
    if m["close"] < C.MIN_PRICE:
        return None
    vres = vcp.analyze(df, dist_52w_high=m["dist_52w_high"])
    rsl = rs.rs_line_metrics(df, index_df)
    # RS 線新高旗標回填 VCP 評分（影響 grade）
    if rsl["rs_line_new_high"]:
        vres = vcp.analyze(df, rs_line_new_high=True, dist_52w_high=m["dist_52w_high"])
    return {
        "stock_id": sid, "name": name, "industry": industry,
        "close": round(m["close"], 2),
        "dist_52w_high": round(m["dist_52w_high"], 4) if pd.notna(m["dist_52w_high"]) else None,
        "liquid": m["liquid"], "avg_turnover_50": round(m["avg_turnover_50"], 0),
        "raw_rs": rs.raw_rs(df, index_df),
        "rs_line_rising": rsl["rs_line_rising"], "rs_line_new_high": rsl["rs_line_new_high"],
        "trend_metrics": m, "vcp": vres.to_dict(),
        # A-6/A-7/A-8 加分欄位（後續接上）
        "fundamental": None, "chips": None, "group_rank": None,
    }


def run(sample: bool = True, as_of: str | None = None) -> dict:
    client = FinMindClient()
    end = as_of or dt.date.today().isoformat()
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=int(C.HISTORY_DAYS * 1.6))).isoformat()
    index_df = client.index_price(C.MARKET_INDEX_ID, start, end)

    uni = build_universe(client, sample)
    rows = []
    for _, u in uni.iterrows():
        r = analyze_stock(client, u["stock_id"], u["stock_name"], u["industry_category"],
                          start, end, index_df)
        if r:
            rows.append(r)

    # 全市場 RS 百分位（demo 為樣本內相對；正式版為全市場）
    raw = pd.Series([r["raw_rs"] for r in rows])
    ratings = rs.percentile_rating(raw)
    for r, rt in zip(rows, ratings):
        r["rs_rating"] = int(rt) if pd.notna(rt) else None

    leaders, ready, breakout = [], [], []
    for r in rows:
        tt = trend_template.evaluate_metrics(r["trend_metrics"], r["rs_rating"])
        price_ok = tt["price_template_ok"]
        rs_ok = tt["conditions"]["8_rs_rating_80"]
        v = r["vcp"]
        rec = _record(r, price_ok)
        if v["today_breakout"] and price_ok and r["liquid"]:
            breakout.append(rec)
        if price_ok and rs_ok and r["liquid"]:
            if v["is_vcp"]:
                leaders.append(rec)
            elif r["dist_52w_high"] is not None and r["dist_52w_high"] >= C.DIST_52W_HIGH_MIN:
                ready.append(rec)

    leaders.sort(key=lambda x: (-_grade_rank(x["grade"]), -(x["rs_rating"] or 0)))
    ready.sort(key=lambda x: -(x["rs_rating"] or 0))

    return {
        "as_of": end,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "universe_scanned": len(rows),
        "counts": {"LEADERS": len(leaders), "READY": len(ready), "BREAKOUT": len(breakout)},
        "LEADERS": leaders, "READY": ready, "BREAKOUT": breakout,
    }


def _record(r, price_ok) -> dict:
    v = r["vcp"]
    return {
        "stock_id": r["stock_id"], "name": r["name"], "industry": r["industry"],
        "close": r["close"], "rs_rating": r["rs_rating"],
        "dist_52w_high": r["dist_52w_high"], "rs_line_new_high": r["rs_line_new_high"],
        "grade": v["grade"], "is_vcp": v["is_vcp"], "pivot_high": round(v["pivot_high"], 2),
        "stop": round(v["stop"], 2), "risk_pct": round(v["risk_pct"], 4),
        "pivot_width": v["pivot_width"], "seq_clean": v["seq_clean"],
        "vol_contraction": v["vol_contraction"], "breakout_status": v["breakout_status"],
        "contractions": v["contractions"],
        "avg_turnover_50": r["avg_turnover_50"], "trend_price_ok": price_ok,
    }


def _grade_rank(g: str) -> int:
    return {"A": 3, "B": 2, "C": 1}.get(g, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="掃全 universe（預設只跑樣本）")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD（回測用）")
    ap.add_argument("--out", default=C.OUTPUT_JSON)
    args = ap.parse_args()
    result = run(sample=not args.full, as_of=args.as_of)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    from src import report
    report.build(args.out, C.OUTPUT_HTML)
    c = result["counts"]
    print(f"as_of={result['as_of']} 掃描 {result['universe_scanned']} 檔 → "
          f"LEADERS {c['LEADERS']} / READY {c['READY']} / BREAKOUT {c['BREAKOUT']}")
    for name in ("LEADERS", "READY", "BREAKOUT"):
        if result[name]:
            print(f"\n[{name}]")
            for x in result[name]:
                print(f"  {x['stock_id']} {x['name']:<6} RS{x['rs_rating']} {x['grade']}級 "
                      f"距高{x['dist_52w_high']:+.1%} 樞紐{x['pivot_high']} 停損{x['stop']} "
                      f"風險{x['risk_pct']:.1%} {x['breakout_status']} {x['industry']}")
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
