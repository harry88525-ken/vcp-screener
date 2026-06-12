# -*- coding: utf-8 -*-
"""L3 深挖：prompt 組裝、無 key 行為、報告渲染與重用。"""
from src import deep_dive


_DIGEST = {
    "stock_id": "2330", "as_of": "2026-06-11",
    "l1l2": {"bucket": "LEADERS", "name": "台積電", "rs_rating": 80, "pivot_width": 0.094},
    "revenue_monthly": [(2026, 5, 4.17e11)],
    "eps": [("2026-03-31", 22.08)],
    "inst": [("2026-06-11", -1.38e6)],
}


def test_to_prompt_contains_key_fields():
    p = deep_dive.to_prompt(_DIGEST)
    assert "2330" in p and "台積電" in p
    assert "22.08" in p          # EPS 進到 prompt
    assert "三大法人" in p        # 籌碼段在


def test_synthesize_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert deep_dive.synthesize("任意 prompt") is None   # 無 key → None，不爆


def test_render_and_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(deep_dive, "ANALYSIS_DIR", str(tmp_path / "analysis"))
    assert deep_dive.is_fresh("2330") is False           # 還沒生成
    path = deep_dive.render_html("2330", "結論先行\n基本面 A+", _DIGEST)
    assert path.endswith("2330.html")
    html = open(path, encoding="utf-8").read()
    assert "台積電" in html and "基本面 A+" in html
    assert deep_dive.is_fresh("2330") is True            # 生成後新鮮 → 重用
    assert deep_dive.is_fresh("2330", reuse_days=0) is False   # 0 天 → 視為過期
