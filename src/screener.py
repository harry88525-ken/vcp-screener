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
import subprocess

import numpy as np
import pandas as pd

import config as C
from src import cache, chips, fundamentals, group_scan, indicators, market_light, rs, trend_template, vcp
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


def analyze_stock(client, sid, name, industry, start, end, index_df, offline: bool = False) -> dict | None:
    """Stage 1：只用價格（趨勢/RS/VCP/流動性）。便宜，掃全市場用。"""
    df = cache.get_price(client, sid, start, end, offline=offline)
    if df.empty or len(df) < C.MA_SLOW + C.MA200_RISING_LOOKBACK:
        return None
    m = indicators.trend_metrics(df)
    if m["close"] < C.MIN_PRICE:
        return None
    rsl = rs.rs_line_metrics(df, index_df)
    vres = vcp.analyze(df, rs_line_new_high=rsl["rs_line_new_high"],
                       dist_52w_high=m["dist_52w_high"], next_target=m["high_52w"])
    closes = df["close"].to_numpy(float)                       # L2：多框架報酬（族群動能）
    frames = {f: (float(closes[-1] / closes[-1 - f] - 1) if len(closes) > f and closes[-1 - f] > 0 else None)
              for f in C.GROUP_FRAMES_DAYS}
    return {
        "stock_id": sid, "name": name, "industry": industry,
        "close": round(m["close"], 2),
        "dist_52w_high": round(m["dist_52w_high"], 4) if pd.notna(m["dist_52w_high"]) else None,
        "liquid": m["liquid"], "avg_turnover_50": round(m["avg_turnover_50"], 0),
        "raw_rs": rs.raw_rs(df, index_df),
        "rs_line_rising": rsl["rs_line_rising"], "rs_line_new_high": rsl["rs_line_new_high"],
        "trend_metrics": m, "vcp": vres.to_dict(), "frames": frames,
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
        r["fundamental"] = fundamentals.evaluate(
            cache.get_fundamental(client.financials, "financials", sid, fin_start, end),
            cache.get_fundamental(client.balance_sheet, "balance", sid, fin_start, end),
            cache.get_fundamental(client.month_revenue, "revenue", sid, fin_start, end))
    except Exception:
        r["fundamental"] = None


def _make_commit_cb():
    """增量 commit：每 flush 一批就 commit+push 快取，逾時也不蒸發。
    只在 Actions（env VCP_INCREMENTAL_COMMIT=1）啟用；本地/測試不推。"""
    if os.environ.get("VCP_INCREMENTAL_COMMIT") != "1":
        return None

    def cb(upto: str):
        for args in (["git", "add", "data/"],
                     ["git", "commit", "-q", "-m", f"data: price cache synced through {upto}"],
                     ["git", "push", "-q"]):
            subprocess.run(args, check=False)
    return cb


def run(sample: bool = True, as_of: str | None = None, limit: int | None = None) -> dict:
    client = FinMindClient()
    end = as_of or dt.date.today().isoformat()
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=int(C.HISTORY_DAYS * 1.6))).isoformat()
    index_df = client.index_price(C.MARKET_INDEX_ID, start, end)
    if not index_df.empty:
        end = index_df["date"].max().date().isoformat()   # 鎖到最新交易日，避免快取「未覆蓋 end」誤判

    uni = build_universe(client, sample, limit)
    use_bulk = not sample                                  # 全市場掃：by-date bulk 預載快取，Stage 1 全程 offline
    if use_bulk and not index_df.empty:
        trading_days = [d.date().isoformat() for d in index_df["date"]]
        cache.sync_bulk(client, trading_days, list(uni["stock_id"]), commit_cb=_make_commit_cb())

    rows = []
    for _, u in uni.iterrows():
        try:
            r = analyze_stock(client, u["stock_id"], u["stock_name"], u["industry_category"],
                              start, end, index_df, offline=use_bulk)
        except Exception:
            r = None                                  # 單檔失敗不中斷全市場掃描
        if r:
            rows.append(r)

    # 全市場 RS 百分位（demo 為樣本內相對；正式版為全市場）
    raw = pd.Series([r["raw_rs"] for r in rows])
    ratings = rs.percentile_rating(raw)
    for r, rt in zip(rows, ratings):
        r["rs_rating"] = int(rt) if pd.notna(rt) else None

    # L2 族群熱點（A-8 雙維度：證交所產業 + 產業鏈主題）+ A-1 市場紅綠燈
    chain_map: dict[str, list[str]] = {}
    if use_bulk:
        chain_df = cache.get_industry_chain(client)
        for sid, sub in zip(chain_df["stock_id"], chain_df["sub_industry"]):
            chain_map.setdefault(sid, []).append(sub)
    group_summary = group_scan.scan(rows, chain_map, index_df)
    above200 = [1 for r in rows
                if np.isfinite(r["trend_metrics"]["ma200"]) and r["close"] > r["trend_metrics"]["ma200"]]
    breadth_pct = len(above200) / len(rows) if rows else 0.0
    market = market_light.evaluate(index_df, breadth_pct)

    # Pass 1：判定候選（不抓資料）
    cands = []
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
        r["_price_ok"], r["_in_breakout"], r["_in_core"], r["_near_high"] = price_ok, in_breakout, in_core, near_high
        cands.append(r)

    # Stage 2：LEADERS/BREAKOUT（主攻標的）一定先 enrich，再用 RS 補滿上限（防全市場逐檔拖死）
    def _enrich_prio(r):
        prime = (r["_in_core"] and r["vcp"]["is_vcp"]) or r["_in_breakout"]
        return (0 if prime else 1, -(r["rs_rating"] or 0))
    cands.sort(key=_enrich_prio)
    for r in cands[:C.ENRICH_MAX_CANDIDATES]:
        enrich_candidate(client, r, end)

    leaders, ready, breakout = [], [], []
    for r in cands:
        rec = _record(r, r["_price_ok"])
        if r["_in_breakout"]:
            breakout.append(rec)
        if r["_in_core"]:
            if r["vcp"]["is_vcp"]:
                leaders.append(rec)
            elif r["_near_high"]:
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
        "groups": group_summary,
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
    # L2：完整族群榜寫獨立 groups.json；leaders.json 只留前段供報告（防膨脹）
    gf = result.get("groups") or {"industries": [], "themes": []}
    with open(C.OUTPUT_GROUPS_JSON, "w", encoding="utf-8") as f:
        json.dump({"as_of": result["as_of"], "generated_at": result["generated_at"],
                   "industries": gf.get("industries", []), "themes": gf.get("themes", [])},
                  f, ensure_ascii=False, indent=2)
    result["groups"] = {"industries": gf.get("industries", [])[:15], "themes": gf.get("themes", [])[:15]}
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
