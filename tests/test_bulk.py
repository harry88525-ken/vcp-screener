# -*- coding: utf-8 -*-
"""by-date bulk 全市場價格：price_by_date 解析 + cache.sync_bulk 續寫/過濾/offline。"""
import pandas as pd

from src import cache
from src.finmind_client import FinMindClient


# ── price_by_date 欄位正規化 ──
def test_price_by_date_parsing(monkeypatch):
    raw = [
        {"date": "2026-06-10", "stock_id": "2330", "Trading_Volume": 1000, "Trading_money": 5e8,
         "open": 100, "max": 110, "min": 99, "close": 108, "spread": 8, "Trading_turnover": 500},
        {"date": "2026-06-10", "stock_id": "2317", "Trading_Volume": 2000, "Trading_money": 6e8,
         "open": 50, "max": 52, "min": 49, "close": 51, "spread": 1, "Trading_turnover": 800},
    ]
    c = FinMindClient(token="x")
    monkeypatch.setattr(c, "_get", lambda *a, **k: raw)
    df = c.price_by_date("2026-06-10")
    assert list(df.columns) == ["date", "stock_id", "open", "high", "low", "close", "volume", "turnover"]
    assert df["high"].tolist() == [110, 52] and df["low"].tolist() == [99, 49]
    assert str(df["date"].dtype).startswith("datetime")


def test_price_by_date_empty(monkeypatch):
    c = FinMindClient(token="x")
    monkeypatch.setattr(c, "_get", lambda *a, **k: [])
    assert c.price_by_date("2026-01-01").empty   # 非交易日


# ── sync_bulk：過濾 universe、續寫、marker、offline 讀取 ──
class _FakeBulkClient:
    """price_by_date 回固定幾檔；'2026-06-06'(週六)當非交易日回空。"""
    def price_by_date(self, date):
        cols = ["date", "stock_id", "open", "high", "low", "close", "volume", "turnover"]
        if date == "2026-06-06":
            return pd.DataFrame(columns=cols)
        rows = []
        for sid, base in (("2330", 100), ("2317", 50), ("9999", 10)):   # 9999 不在 universe
            rows.append({"date": pd.Timestamp(date), "stock_id": sid, "open": base, "high": base + 2,
                         "low": base - 1, "close": base + 1, "volume": 1000, "turnover": 5e8})
        return pd.DataFrame(rows, columns=cols)


def _isolate_cache(monkeypatch, tmp_path):
    d = tmp_path / "prices"
    monkeypatch.setattr(cache, "PRICE_DIR", str(d))
    monkeypatch.setattr(cache, "SYNC_MARKER", str(d / "_synced_through.txt"))


def test_sync_bulk_filters_universe_and_marker(monkeypatch, tmp_path):
    _isolate_cache(monkeypatch, tmp_path)
    fake = _FakeBulkClient()
    days = ["2026-06-04", "2026-06-05", "2026-06-08"]          # 三個交易日
    n = cache.sync_bulk(fake, days, ["2330", "2317"], chunk=2)
    assert n == 3
    assert cache.read_marker() == "2026-06-08"

    # universe 內的有檔、universe 外的 9999 沒寫
    p2330 = cache.get_price(fake, "2330", "2026-01-01", "2026-06-08", offline=True)
    assert len(p2330) == 3 and p2330["close"].tolist() == [101, 101, 101]
    assert cache.get_price(fake, "9999", "2026-01-01", "2026-06-08", offline=True).empty

    # 二次同步：marker 已到最新 → 0 抓取（idempotent）
    assert cache.sync_bulk(fake, days, ["2330", "2317"], chunk=2) == 0


def test_get_fundamental_cache_hit_and_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "FUND_DIR", str(tmp_path / "fund"))
    calls = {"n": 0}

    def fetch(sid, start, end):
        calls["n"] += 1
        return pd.DataFrame({"date": pd.to_datetime(["2026-03-31"]), "type": ["EPS"], "value": [1.5]})

    # 第一次抓、第二次（新鮮）走快取
    cache.get_fundamental(fetch, "financials", "2330", "2024-01-01", "2026-06-11", stale_days=10)
    df = cache.get_fundamental(fetch, "financials", "2330", "2024-01-01", "2026-06-11", stale_days=10)
    assert calls["n"] == 1 and df["value"].tolist() == [1.5]

    # stale_days=0 → 視為過期、重抓
    cache.get_fundamental(fetch, "financials", "2330", "2024-01-01", "2026-06-11", stale_days=0)
    assert calls["n"] == 2

    # 不同 dataset / 不同股票各自獨立快取
    cache.get_fundamental(fetch, "revenue", "2330", "2024-01-01", "2026-06-11", stale_days=10)
    cache.get_fundamental(fetch, "financials", "2317", "2024-01-01", "2026-06-11", stale_days=10)
    assert calls["n"] == 4


def test_sync_bulk_incremental_append(monkeypatch, tmp_path):
    _isolate_cache(monkeypatch, tmp_path)
    fake = _FakeBulkClient()
    cache.sync_bulk(fake, ["2026-06-04"], ["2330"])
    cache.sync_bulk(fake, ["2026-06-04", "2026-06-05"], ["2330"])   # 只補 06-05
    df = cache.get_price(fake, "2330", "2026-01-01", "2026-06-05", offline=True)
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-06-04", "2026-06-05"]   # 去重、升序
