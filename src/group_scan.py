# -*- coding: utf-8 -*-
"""
L2 族群熱點（A-8 獨立層）
=========================
一檔 ~49% 漲幅來自族群。把「族群」從個股加分欄位升級成獨立一層，回答：
  哪個族群最強 / 是真族群還是孤狼 / 強多久了。

Hybrid 雙維度：
  - 產業（證交所 industry_category）：1 對 1、乾淨完整 → 結構底
  - 主題（產業鏈 sub_industry）：多對多、更細 → 主題疊加

每個族群算：成員 median RS、廣度（站上 200MA %）、一致性（≥3 檔 VCP/近高=真族群）、
多框架動能（21/63/126 日成員報酬中位數）、相對大盤（動能−大盤同期報酬=族群 RS 線方向）。
綜合分數排名，前段班回灌個股 VCP 評分 +1。純運算、吃 L1 的乾淨 row 合約。
"""
from __future__ import annotations

import numpy as np

import config as C

FRAMES = C.GROUP_FRAMES_DAYS


def _index_frame_returns(index_df) -> dict:
    """大盤各框架報酬，作為族群 RS 線的分母基準。"""
    out = {f: None for f in FRAMES}
    if index_df is None or index_df.empty:
        return out
    ic = index_df["close"].to_numpy(float)
    for f in FRAMES:
        if len(ic) > f and ic[-1 - f] > 0:
            out[f] = float(ic[-1] / ic[-1 - f] - 1)
    return out


def _near_high(m: dict) -> bool:
    d = m.get("dist_52w_high")
    return d is not None and d >= C.DIST_52W_HIGH_MIN


def rank_dimension(rows: list[dict], key_fn, index_returns: dict,
                   min_members: int = 1, top_frac: float = None) -> list[dict]:
    """通用族群排名。key_fn(row)→該 row 所屬的族群鍵 list（支援多對多）。"""
    top_frac = top_frac if top_frac is not None else C.GROUP_TOP_FRAC
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        for k in key_fn(r):
            if k:
                buckets.setdefault(k, []).append(r)

    stats = []
    for name, members in buckets.items():
        if len(members) < min_members:
            continue
        rs_vals = [m["rs_rating"] for m in members if m.get("rs_rating") is not None]
        if not rs_vals:
            continue
        median_rs = float(np.median(rs_vals))
        healthy = sum(1 for m in members
                      if np.isfinite(m["trend_metrics"]["ma200"])
                      and m["trend_metrics"]["close"] > m["trend_metrics"]["ma200"])
        breadth = healthy / len(members)
        vcp_count = sum(1 for m in members if m["vcp"]["is_vcp"] or _near_high(m))

        mom, rs_vs = {}, {}
        for f in FRAMES:
            vals = [m["frames"][f] for m in members
                    if m.get("frames") and m["frames"].get(f) is not None]
            mom[f] = float(np.median(vals)) if vals else None
            ir = (index_returns or {}).get(f)
            rs_vs[f] = (mom[f] - ir) if (mom[f] is not None and ir is not None) else None

        # 綜合分數：強度(median RS) + 廣度 + 近月相對大盤（族群 RS 線方向，抓新熱點）
        score = median_rs + breadth * 10
        if rs_vs.get(FRAMES[0]) is not None:
            score += max(-12.0, min(12.0, rs_vs[FRAMES[0]] * 100 * 0.5))

        ranked_mem = sorted([m for m in members if m.get("rs_rating") is not None],
                            key=lambda m: m["rs_rating"], reverse=True)[:5]
        top_members = [{"stock_id": m.get("stock_id"), "name": m.get("name"), "rs": m["rs_rating"]}
                       for m in ranked_mem]

        stats.append({
            "name": name, "members": len(members),
            "median_rs": round(median_rs, 1), "breadth": round(breadth, 3),
            "vcp_count": vcp_count, "real_group": bool(vcp_count >= C.GROUP_CONSISTENCY_MIN),
            "mom": {str(f): (round(mom[f], 4) if mom[f] is not None else None) for f in FRAMES},
            "rs_vs_mkt": {str(f): (round(rs_vs[f], 4) if rs_vs[f] is not None else None) for f in FRAMES},
            "score": round(score, 2), "top_members": top_members,
        })

    stats.sort(key=lambda s: -s["score"])
    cut = max(1, int(len(stats) * top_frac))
    for i, s in enumerate(stats):
        s["rank"] = i + 1
        s["top"] = bool(i < cut)
    return stats


def scan(rows: list[dict], chain_map: dict, index_df) -> dict:
    """雙維度族群掃描 + 回灌個股標註。回傳 {industries, themes}。"""
    idx_ret = _index_frame_returns(index_df)
    industries = rank_dimension(rows, lambda r: [r.get("industry") or "其他"], idx_ret)
    themes = rank_dimension(rows, lambda r: chain_map.get(r.get("stock_id"), []), idx_ret,
                            min_members=C.GROUP_THEME_MIN_MEMBERS)

    ind_by = {s["name"]: s for s in industries}
    top_themes = {s["name"] for s in themes if s["top"]}
    for r in rows:
        g = ind_by.get(r.get("industry") or "其他")
        if g:
            r["group_rank"] = g["rank"]
            r["group_median_rs"] = g["median_rs"]
            r["group_consistency"] = g["vcp_count"]
            r["group_real"] = g["real_group"]
            r["group_top"] = g["top"]
        else:
            r["group_rank"] = None
            r["group_top"] = False
            r["group_real"] = False
            r["group_consistency"] = 0
        my = [t for t in chain_map.get(r.get("stock_id"), []) if t in top_themes]
        r["theme_top"] = bool(my)
        r["top_theme"] = my[0] if my else None
    return {"industries": industries, "themes": themes}


def annotate(rows: list[dict]) -> list[dict]:
    """相容介面：只用產業維度標註個股（無多框架/大盤）。回傳產業排名。"""
    return scan(rows, {}, None)["industries"]
