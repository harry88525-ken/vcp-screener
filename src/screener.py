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

import numpy as np
import pandas as pd

import config as C
from src import cache, chips, fundamentals, groups, indicators, market_light, rs, trend_template, vcp
from src.finmind_client import FinMindClient

# 示範用流動性權值股清單（正式版改掃全 universe）
SAMPLE = ["2330", "2317", "2454", "2382", "2308", "2303", "3711", "2891",
          "2412", "2882", "1301", "2002", "2207", "3008", "2379", "2357",
          "4938", "2395", "2603", "3017", "3037", "6669", "3231", "2376", "1216"]


def build_universe(client: FinMindClient, sample: bool, limit: int | None = None) -> pd.DataFrame:
    uni = client.universe()
    uni = uni[uni["type"].isin(["twse", "tpex"])]
    uni = uni[~uni["industry_category"].isin(C.EXCLUDE_INDUSTRIES)]
    uni = uni[uni["stock_id"].str.match(r"^\d{4}$")]          # 只留 4 位數普通股代號（濾掉權證/特別股）
    if sample:
        uni = uni[uni["stock_id"].isin(SAMPLE)]
    uni = uni.sort_values("stock_id").reset_index(drop=True)
    if limit:
        uni = uni.head(limit)
    return uni.reset_index(drop=True)


def _recent(end: str, days: int) -> str:
    return (dt.date.fromisoformat(end) - dt.timedelta(days=days)).isoformat()


def analyze_stock(client, sid, name, industry, start, end, index_df) -> dict | None:
    """Stage 1：只用價格（趨勢/RS/VCP/流動性）。便宜，掃全市場用。"""
    df = cache.get_price(client, sid, start, end)
    if df.empty or len(df) < C.MA_SLOW + C.MA200_RISING_LOOKBACK:
        return None
    m = indicators.trend_metrics(df)
    if m["close"] < C.MIN_PRICE:
        return None
    rsl = rs.rs_line_metrics(df, index_df)
    vres = vcp.analyze(df, rs_line_new_high=rsl["rs_line_new_high"],
                       dist_52w_high=m["dist_52w_high"], next_target=m["high_52w"])
    return {
        "stock_id": sid, "name": name, "industry": industry,
        "close": round(m["close"], 2),
        "dist_52w_high": round(m["dist_52w_high"], 4) if pd.notna(m["dist_52w_high"]) else None,
        "liquid": m["liquid"], "avg_turnover_50": round(m["avg_turnover_50"], 0),
        "raw_rs": rs.raw_rs(df, index_df),
        "rs_line_rising": rsl["rs_line_rising"], "rs_line_new_high": rsl["rs_line_new_high"],
        "trend_metrics": m, "vcp": vres.to_dict(),
        "fundamental": None, "chips": None,
    }


def enrich_candidate(client, r: dict, end: str) -> None:
    """Stage 2：只對入選候選（通過核心門檻者）抓 A-6/A-7 資料。就地寫回。"""
    sid = r["stock_id"]
    fin_start = _recent(end, 1100)
    try:
        r["chips"] = chips.evaluate(client.institutional(sid, _recent(end, 40), end),
                                    client.margin(sid, _recent(end, 40), end))
    except Exception:
        r["chips"] = None
    try:
        r["fundamental"] = fundamentals.evaluate(client.financials(sid, fin_start, end),
                                                 client.balance_sheet(sid, fin_start, end),
                                                 client.month_revenue(sid, fin_start, end))
    except Exception:
        r["fundamental"] = None


def run(sample: bool = True, as_of: str | None = None, limit: int | None = None) -> dict:
    client = FinMindClient()
    end = as_of or dt.date.today().isoformat()
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=int(C.HISTORY_DAYS * 1.6))).isoformat()
    index_df = client.index_price(C.MARKET_INDEX_ID, start, end)

    uni = build_universe(client, sample, limit)
    rows = []
    for _, u in uni.iterrows():
        try:
            r = analyze_stock(client, u["stock_id"], u["stock_name"], u["industry_category"],
                              start, end, index_df)
        except Exception:
            r = None                                  # 單檔失敗不中斷全市場掃描
        if r:
            rows.append(r)

    # 全市場 RS 百分位（demo 為樣本內相對；正式版為全市場）
    raw = pd.Series([r["raw_rs"] for r in rows])
    ratings = rs.percentile_rating(raw)
    for r, rt in zip(rows, ratings):
        r["rs_rating"] = int(rt) if pd.notna(rt) else None

    # A-8 族群熱點（跨個股）+ A-1 市場紅綠燈
    group_summary = groups.annotate(rows)
    above200 = [1 for r in rows
                if np.isfinite(r["trend_metrics"]["ma200"]) and r["close"] > r["trend_metrics"]["ma200"]]
    breadth_pct = len(above200) / len(rows) if rows else 0.0
    market = market_light.evaluate(index_df, breadth_pct)

    leaders, ready, breakout = [], [], []
    for r in rows:
        tt = trend_template.evaluate_metrics(r["trend_metrics"], r["rs_rating"])
        price_ok = tt["price_template_ok"]
        rs_ok = tt["conditions"]["8_rs_rating_80"]
        v = r["vcp"]
        near_high = r["dist_52w_high"] is not None and r["dist_52w_high"] >= C.DIST_52W_HIGH_MIN
        in_breakout = v["today_breakout"] and price_ok and r["liquid"]
        in_core = price_ok and rs_ok and r["liquid"] and (v["is_vcp"] or near_high)
        if not (in_breakout or in_core):
            continue
        enrich_candidate(client, r, end)             # Stage 2：只對候選抓加分資料
        rec = _record(r, price_ok)
        if in_breakout:
            breakout.append(rec)
        if in_core:
            if v["is_vcp"]:
                leaders.append(rec)
            elif near_high:
                ready.append(rec)

    leaders.sort(key=lambda x: (-_grade_rank(x["grade"]), -(x["rs_rating"] or 0)))
    ready.sort(key=lambda x: -(x["rs_rating"] or 0))

    return {
        "as_of": end,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "universe_scanned": len(rows),
        "market": market,
        "counts": {"LEADERS": len(leaders), "READY": len(ready), "BREAKOUT": len(breakout)},
        "LEADERS": leaders, "READY": ready, "BREAKOUT": breakout,
        "groups_top": group_summary[:8],
    }


def _bump_grade(grade: str, group_top: bool) -> str:
    """A-8：屬前段班族群 → VCP 評分 +1 級。"""
    if not group_top or grade not in ("B", "C"):
        return grade
    return {"C": "B", "B": "A"}[grade]


def _record(r, price_ok) -> dict:
    v = r["vcp"]
    f = r.get("fundamental") or {}
    ch = r.get("chips") or {}
    grade = _bump_grade(v["grade"], r.get("group_top", False))
    return {
        "stock_id": r["stock_id"], "name": r["name"], "industry": r["industry"],
        "close": r["close"], "rs_rating": r["rs_rating"],
        "dist_52w_high": r["dist_52w_high"], "rs_line_new_high": r["rs_line_new_high"],
        "grade": grade, "is_vcp": v["is_vcp"], "pivot_high": round(v["pivot_high"], 2),
        "stop": round(v["stop"], 2), "risk_pct": round(v["risk_pct"], 4),
        "reward_risk": v.get("reward_risk", 0), "pivot_width": v["pivot_width"],
        "seq_clean": v["seq_clean"], "vol_contraction": v["vol_contraction"],
        "breakout_status": v["breakout_status"], "contractions": v["contractions"],
        "avg_turnover_50": r["avg_turnover_50"], "trend_price_ok": price_ok,
        # A-6/A-7/A-8 加分欄位
        "inst_net_buy": ch.get("inst_net_buy"), "trust_streak": ch.get("trust_streak"),
        "margin_overheat": ch.get("margin_overheat"),
        "eps_yoy": f.get("eps_yoy"), "roe": f.get("roe"), "rev_3m_yoy": f.get("rev_3m_yoy"),
        "fundamental_ok": f.get("fundamental_ok"),
        "group_rank": r.get("group_rank"), "group_real": r.get("group_real"),
        "group_top": r.get("group_top"),
    }


def _grade_rank(g: str) -> int:
    return {"A": 3, "B": 2, "C": 1}.get(g, 0)


def _load_prev(path: str) -> dict | None:
    """覆寫前讀上一次（昨日）結果，作為 CHANGES 比較基準。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def compute_changes(current: dict, prev: dict | None) -> dict:
    """逐清單 diff：新進(今有昨無) / 離開(昨有今無)。"""
    out = {"vs": prev.get("as_of") if prev else None}
    for key in ("LEADERS", "READY", "BREAKOUT"):
        cur = {x["stock_id"]: x for x in current.get(key, [])}
        old = {x["stock_id"]: x for x in (prev.get(key, []) if prev else [])}
        out[key] = {
            "entered": [{"stock_id": s, "name": cur[s]["name"],
                         "grade": cur[s].get("grade", "")} for s in cur if s not in old],
            "left": [{"stock_id": s, "name": old[s]["name"]} for s in old if s not in cur],
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="掃全 universe（預設只跑樣本）")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD（回測用）")
    ap.add_argument("--limit", type=int, default=None, help="限制 universe 檔數（測試用）")
    ap.add_argument("--out", default=C.OUTPUT_JSON)
    args = ap.parse_args()
    prev = _load_prev(args.out)                       # 覆寫前先讀昨日結果
    result = run(sample=not args.full, as_of=args.as_of, limit=args.limit)
    result["changes"] = compute_changes(result, prev)
    out_dir = os.path.dirname(args.out)
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    # 歷史快照（供日後任意日比較）
    hist_dir = os.path.join(out_dir, "history")
    os.makedirs(hist_dir, exist_ok=True)
    with open(os.path.join(hist_dir, f"{result['as_of']}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    from src import report
    report.build(args.out, C.OUTPUT_HTML)
    ch = result["changes"]
    if ch["vs"]:
        print(f"CHANGES vs {ch['vs']}: "
              + " / ".join(f"{k} +{len(ch[k]['entered'])} -{len(ch[k]['left'])}"
                           for k in ("LEADERS", "READY", "BREAKOUT")))
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
