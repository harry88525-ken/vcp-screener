# -*- coding: utf-8 -*-
"""
A-8 族群熱點（= L2 雛形）
========================
一檔漲幅 ~49% 來自族群。讓最強股帶你找到強勢族群，並確認「族群的事 vs 孤狼」。
- 族群強度排名：成員 RS 中位數 + 廣度（站上 200MA 家數%），全產業排名
- 族群一致性：同產業 ≥3 檔 is_vcp/近高 = 真族群
- 個股族群加分：屬前段班族群 → VCP 評分 +1 級（由 screener 套用）

跨個股運算，於 screener 收集完所有個股指標後呼叫。就地 annotate 每筆 row。
"""
from __future__ import annotations

import numpy as np

import config as C


def annotate(rows: list[dict]) -> list[dict]:
    """rows：每筆含 industry / rs_rating / trend_metrics / vcp。回傳族群摘要（並就地寫回 row）。"""
    by_ind: dict[str, list[dict]] = {}
    for r in rows:
        by_ind.setdefault(r.get("industry") or "其他", []).append(r)

    stats = []
    for ind, members in by_ind.items():
        rs_vals = [m["rs_rating"] for m in members if m.get("rs_rating") is not None]
        if not rs_vals:
            continue
        median_rs = float(np.median(rs_vals))
        healthy = sum(1 for m in members
                      if np.isfinite(m["trend_metrics"]["ma200"])
                      and m["trend_metrics"]["close"] > m["trend_metrics"]["ma200"])
        breadth = healthy / len(members)
        vcp_count = sum(1 for m in members if m["vcp"]["is_vcp"])
        stats.append({"industry": ind, "members": len(members), "median_rs": round(median_rs, 1),
                      "breadth": round(breadth, 3), "vcp_count": vcp_count,
                      "score": median_rs + breadth * 10})

    stats.sort(key=lambda s: -s["score"])
    n = len(stats)
    top_cut = max(1, int(n * C.GROUP_TOP_FRAC))
    rank_of = {}
    for i, s in enumerate(stats):
        s["rank"] = i + 1
        rank_of[s["industry"]] = s

    for r in rows:
        ind = r.get("industry") or "其他"
        g = rank_of.get(ind)
        if g:
            r["group_rank"] = g["rank"]
            r["group_median_rs"] = g["median_rs"]
            r["group_consistency"] = g["vcp_count"]
            r["group_real"] = bool(g["vcp_count"] >= C.GROUP_CONSISTENCY_MIN)
            r["group_top"] = bool(g["rank"] <= top_cut)
        else:
            r["group_rank"] = None
            r["group_top"] = False
            r["group_real"] = False
            r["group_consistency"] = 0
    return stats
