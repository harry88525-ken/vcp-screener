# -*- coding: utf-8 -*-
"""CHANGES diff 邏輯（今/昨清單比較）。"""
from src import screener


def test_compute_changes_entered_and_left():
    prev = {"as_of": "2026-06-10",
            "LEADERS": [{"stock_id": "2330", "name": "台積電"}],
            "READY": [{"stock_id": "2454", "name": "聯發科"}], "BREAKOUT": []}
    cur = {"as_of": "2026-06-11",
           "LEADERS": [{"stock_id": "2330", "name": "台積電"},
                       {"stock_id": "3037", "name": "欣興", "grade": "B"}],
           "READY": [], "BREAKOUT": []}
    ch = screener.compute_changes(cur, prev)
    assert ch["vs"] == "2026-06-10"
    assert {x["stock_id"] for x in ch["LEADERS"]["entered"]} == {"3037"}
    assert ch["LEADERS"]["left"] == []
    assert {x["stock_id"] for x in ch["READY"]["left"]} == {"2454"}


def test_compute_changes_no_prev_is_safe():
    ch = screener.compute_changes({"LEADERS": [], "READY": [], "BREAKOUT": []}, None)
    assert ch["vs"] is None
    assert ch["LEADERS"] == {"entered": [], "left": []}
