# -*- coding: utf-8 -*-
"""L3 報告渲染（離線版）：不呼叫 FinMind，digest 直接由 leaders.json 組出。
用法：python render_l3_offline.py <stock_id> <report_md_path>
render_html 只用到 digest['as_of'] 與 digest['l1l2']['name']，故此路徑等效。"""
import json, sys
from src import deep_dive

sid = sys.argv[1]
md = open(sys.argv[2], encoding="utf-8").read()
d = json.load(open("docs/leaders.json", encoding="utf-8"))
name = ""
for bucket in ("LEADERS", "READY", "BREAKOUT"):
    for x in d.get(bucket, []):
        if x["stock_id"] == sid:
            name = x.get("name", "")
            break
    if name:
        break
digest = {"as_of": d["as_of"], "l1l2": {"name": name}}
print("OK", deep_dive.render_html(sid, md, digest))
