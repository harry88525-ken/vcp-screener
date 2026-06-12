# -*- coding: utf-8 -*-
"""L2 族群熱點：雙維度排名、多框架動能、產業鏈主題多對多。"""
import numpy as np
import pandas as pd

from src import group_scan


def _row(sid, ind, rs, vcp, f21, dist=-0.10):
    return {"stock_id": sid, "industry": ind, "rs_rating": rs,
            "trend_metrics": {"close": 100.0, "ma200": 90.0}, "vcp": {"is_vcp": vcp},
            "dist_52w_high": dist, "frames": {21: f21, 63: 0.10, 126: 0.20}}


def _index():
    return pd.DataFrame({"close": np.linspace(100, 103, 130)})   # 大盤 ~3%/窗口


def test_industry_rank_momentum_and_writeback():
    rows = [_row("A1", "半導體", 95, True, 0.12), _row("A2", "半導體", 92, True, 0.11),
            _row("A3", "半導體", 90, True, 0.10), _row("B1", "航運", 40, False, -0.02),
            _row("B2", "航運", 38, False, -0.01)]
    res = group_scan.scan(rows, {}, _index())
    semi = next(s for s in res["industries"] if s["name"] == "半導體")
    ship = next(s for s in res["industries"] if s["name"] == "航運")
    assert semi["rank"] == 1 and semi["top"] is True
    assert semi["real_group"] is True                       # ≥3 檔 VCP
    assert semi["mom"]["21"] is not None and semi["rs_vs_mkt"]["21"] is not None
    assert semi["score"] > ship["score"]                    # 強族群分數高
    assert rows[0]["group_rank"] == 1 and rows[0]["group_top"] is True   # 回灌個股


def test_theme_many_to_many_and_min_members():
    rows = [_row(str(i), "電子", 90, True, 0.10) for i in range(4)]
    chain = {"0": ["主題X"], "1": ["主題X"], "2": ["主題X", "主題Y"], "3": ["主題Z"]}
    res = group_scan.scan(rows, chain, _index())
    names = [t["name"] for t in res["themes"]]
    assert "主題X" in names              # 3 檔 → 入榜
    assert "主題Y" not in names          # 1 檔 < min 3，濾掉
    assert "主題Z" not in names
    x = next(t for t in res["themes"] if t["name"] == "主題X")
    assert x["members"] == 3
    assert rows[2]["theme_top"] is True and rows[2]["top_theme"] == "主題X"   # 多對多回灌


def test_annotate_compat_no_frames():
    # 舊式 row（無 frames/stock_id）→ annotate 不爆，產業排名仍可用
    rows = [{"industry": "鋼鐵", "rs_rating": 80, "trend_metrics": {"close": 100, "ma200": 95},
             "vcp": {"is_vcp": False}}]
    out = group_scan.annotate(rows)
    assert out[0]["name"] == "鋼鐵" and rows[0]["group_rank"] == 1
