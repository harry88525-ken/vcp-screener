# -*- coding: utf-8 -*-
"""L3 報告渲染小工具：python render_l3.py <stock_id> <report_md_path>"""
import sys
from src import deep_dive
from src.finmind_client import FinMindClient

sid = sys.argv[1]
md = open(sys.argv[2], encoding="utf-8").read()
d = deep_dive.gather(FinMindClient(), sid)
p = deep_dive.render_html(sid, md, d)
print("OK", p)
