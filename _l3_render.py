"""L3 批次 render：讀 scratchpad/reports/{id}.md → docs/analysis/{id}.html"""
import sys, io, os
from src import deep_dive
from src.finmind_client import FinMindClient

MD_DIR = sys.argv[1]
ids = sys.argv[2:]
c = FinMindClient()
for sid in ids:
    p = os.path.join(MD_DIR, f"{sid}.md")
    txt = io.open(p, encoding="utf-8").read()
    d = deep_dive.gather(c, sid)
    out = deep_dive.render_html(sid, txt, d)
    print("OK", sid, out)
