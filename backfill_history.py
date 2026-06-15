# -*- coding: utf-8 -*-
"""一次性：往過去 backfill 全市場日線（補 backtest 樣本到約 3 年）。

現有快取最早 2024-03-27 → 往前補 2023-06-01 ~ 2024-03-26。
用 by-date bulk（Backer）逐交易日抓 → merge 進 data/prices（cache._merge_to_disk 去重排序）。
不動 _synced_through.txt marker（那是「往未來」同步用）。分塊 flush 防中途失敗蒸發。
用法：set -a; . ./.env; set +a; .venv/Scripts/python.exe backfill_history.py
"""
from __future__ import annotations
import glob
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd

import config as C
from src.finmind_client import FinMindClient
from src import cache

START = "2023-06-01"
END = "2024-03-26"          # 接上現有快取最早 2024-03-27
FLUSH_EVERY = 40            # 每 40 個交易日 flush 一次磁碟


def main():
    client = FinMindClient()
    idx = client.index_price(C.MARKET_INDEX_ID, START, END)   # 用加權指數有資料的日子 = 開市日
    if idx.empty:
        print("❌ 拿不到交易日（index_price 空）", flush=True)
        return
    days = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in idx["date"]]
    universe_ids = {os.path.basename(p)[:-8] for p in glob.glob(os.path.join("data", "prices", "*.parquet"))}
    print(f"要補 {len(days)} 個交易日：{days[0]} ~ {days[-1]}；universe {len(universe_ids)} 檔", flush=True)

    buf, done, rows_total = [], 0, 0
    for i, d in enumerate(days):
        df = client.price_by_date(d)
        if not df.empty:
            buf.append(df)
            rows_total += len(df)
        done += 1
        if (i + 1) % FLUSH_EVERY == 0:
            if buf:
                cache._merge_to_disk(pd.concat(buf, ignore_index=True), universe_ids)
                buf.clear()
            print(f"  …{i+1}/{len(days)} 已 flush（累計 {rows_total} 列）", flush=True)
    if buf:
        cache._merge_to_disk(pd.concat(buf, ignore_index=True), universe_ids)
    print(f"✅ backfill 完成：{done} 個交易日、{rows_total} 列已併入 data/prices", flush=True)


if __name__ == "__main__":
    main()
