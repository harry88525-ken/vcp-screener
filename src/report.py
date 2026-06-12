# -*- coding: utf-8 -*-
"""
報告產生器：docs/leaders.json → docs/index.html
固定樣式，只換內容（KEN 偏好）。運算與呈現分離——本檔只讀 JSON，不算任何指標。
"""
from __future__ import annotations

import json
import sys

from jinja2 import Template

import config as C

TEMPLATE = Template("""<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VCP 選股 · {{ d.as_of }}</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--mut:#8b949e;
--a:#3fb950;--b:#58a6ff;--c:#d29922;--red:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,"Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.cards{display:flex;gap:10px;margin-bottom:22px;flex-wrap:wrap}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:120px}
.kpi b{font-size:26px;display:block}.kpi span{color:var(--mut);font-size:12px}
section{margin-bottom:28px}h2{font-size:16px;border-left:3px solid var(--b);padding-left:8px}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),td.l{text-align:left}
th{color:var(--mut);font-weight:600;background:#11161d}
tr:last-child td{border-bottom:none}
.g{font-weight:700;border-radius:4px;padding:1px 7px}.gA{background:var(--a);color:#04260f}
.gB{background:var(--b);color:#04203f}.gC{background:var(--c);color:#241a00}
.flag{color:var(--a);font-size:11px}.muted{color:var(--mut)}.empty{color:var(--mut);padding:14px;background:var(--card);border-radius:10px}
.chg{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.chgrow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.chgk{color:var(--mut);font-size:12px;min-width:80px}
.ent{background:rgba(63,185,80,.16);color:var(--a);border-radius:4px;padding:2px 8px;font-size:12px}
.lft{background:rgba(248,81,73,.16);color:var(--red);border-radius:4px;padding:2px 8px;font-size:12px}
.mkt{display:flex;gap:14px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:18px;font-size:13px}
.mkt b{font-size:15px}.tag{font-size:11px;border-radius:4px;padding:1px 6px;margin-right:4px}
.tg{background:rgba(63,185,80,.16);color:var(--a)}.to{background:rgba(210,153,34,.18);color:var(--c)}
.hot{display:flex;gap:16px;flex-wrap:wrap}.hotcol{flex:1;min-width:330px}
.hoth{font-size:13px;color:var(--mut);margin:0 0 6px}.up{color:var(--a)}.dn{color:var(--red)}
.foot{color:var(--mut);font-size:11px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}
</style></head><body><div class="wrap">
<h1>📈 VCP 選股大腦 · L1</h1>
<div class="sub">as-of {{ d.as_of }}　|　掃描 {{ d.universe_scanned }} 檔　|　產生 {{ d.generated_at }}　|　緊度派</div>
{% if d.market %}{% set lt = {'green':['🟢','綠燈','var(--a)'],'yellow':['🟡','黃燈','var(--c)'],'red':['🔴','紅燈','var(--red)']}[d.market.light] %}
<div class="mkt"><b style="color:{{ lt[2] }}">{{ lt[0] }} 市場{{ lt[1] }}</b>
<span>廣度 {{ '%.0f%%'|format(d.market.breadth_pct*100) }}</span>
<span>倉位係數 ×{{ d.market.position_factor }}</span>
<span class="muted">A-1 合成 {{ d.market.score }}（②Follow-through ④派發日 待補；廣度需全市場才準）</span></div>{% endif %}
<div class="cards">
  <div class="kpi"><b style="color:var(--a)">{{ d.counts.LEADERS }}</b><span>LEADERS 主攻</span></div>
  <div class="kpi"><b style="color:var(--b)">{{ d.counts.READY }}</b><span>READY 觀察</span></div>
  <div class="kpi"><b style="color:var(--c)">{{ d.counts.BREAKOUT }}</b><span>BREAKOUT 突破</span></div>
</div>

<section><h2>📋 與昨日變化{% if d.changes and d.changes.vs %}（vs {{ d.changes.vs }}）{% endif %}</h2>
{% if not d.changes or not d.changes.vs %}<div class="empty">首次執行，無比較基準。</div>
{% else %}
{% set anychg = d.changes.LEADERS.entered or d.changes.LEADERS.left or d.changes.READY.entered or d.changes.READY.left or d.changes.BREAKOUT.entered or d.changes.BREAKOUT.left %}
<div class="chg">
{% if not anychg %}<span class="muted">與昨日相同，無進出。</span>{% endif %}
{% for key in ['LEADERS','READY','BREAKOUT'] %}{% set c = d.changes[key] %}{% if c.entered or c.left %}
<div class="chgrow"><span class="chgk">{{ key }}</span>
{% for x in c.entered %}<span class="ent">+ {{ x.stock_id }} {{ x.name }}{% if x.grade %} {{ x.grade }}{% endif %}</span>{% endfor %}
{% for x in c.left %}<span class="lft">− {{ x.stock_id }} {{ x.name }}</span>{% endfor %}
</div>{% endif %}{% endfor %}
</div>
{% endif %}</section>

{% macro trade_table(rows) %}
<table><thead><tr>
<th>代號</th><th>名稱</th><th>產業</th><th>RS</th><th>評分</th><th>收盤</th><th>距52高</th>
<th>樞紐(買點)</th><th>停損</th><th>風險%</th><th>R:R</th><th>狀態</th><th>加分</th></tr></thead><tbody>
{% for x in rows %}<tr>
<td>{{ x.stock_id }}</td><td>{{ x.name }}</td><td class="l muted">{{ x.industry }}</td>
<td>{{ x.rs_rating }}</td><td><span class="g g{{ x.grade }}">{{ x.grade }}</span></td>
<td>{{ x.close }}</td><td>{{ '%+.1f%%'|format(x.dist_52w_high*100) }}</td>
<td>{{ x.pivot_high }}</td><td>{{ x.stop }}</td><td>{{ '%.1f%%'|format(x.risk_pct*100) }}</td>
<td>{{ '%.1f'|format(x.reward_risk or 0) }}</td>
<td>{{ x.breakout_status }}</td>
<td class="l">{% if x.inst_net_buy %}<span class="tag tg">法人買</span>{% endif %}
{% if x.trust_streak and x.trust_streak >= 3 %}<span class="tag tg">投信連{{ x.trust_streak }}</span>{% endif %}
{% if x.fundamental_ok %}<span class="tag tg">基本面</span>{% endif %}
{% if x.rs_line_new_high %}<span class="tag tg">RS新高</span>{% endif %}
{% if x.group_top %}<span class="tag to">族群#{{ x.group_rank }}</span>{% endif %}</td>
</tr>{% endfor %}</tbody></table>
{% endmacro %}

{% macro group_table(gs) %}
{% if gs %}<table><thead><tr><th>#</th><th>族群</th><th>檔</th><th>中位RS</th><th>廣度</th><th>1月</th><th>3月</th><th>真族群</th></tr></thead><tbody>
{% for g in gs %}<tr>
<td>{{ g.rank }}</td><td class="l">{{ g.name }}{% if g.top %} <span class="tag to">熱</span>{% endif %}</td>
<td>{{ g.members }}</td><td>{{ g.median_rs }}</td><td>{{ '%.0f%%'|format(g.breadth*100) }}</td>
<td class="{{ 'up' if g.mom['21'] and g.mom['21'] > 0 else 'dn' }}">{{ '%+.1f%%'|format(g.mom['21']*100) if g.mom['21'] is not none else '—' }}</td>
<td class="{{ 'up' if g.mom['63'] and g.mom['63'] > 0 else 'dn' }}">{{ '%+.1f%%'|format(g.mom['63']*100) if g.mom['63'] is not none else '—' }}</td>
<td>{% if g.real_group %}<span class="flag">✓ {{ g.vcp_count }}</span>{% endif %}</td>
</tr>{% endfor %}</tbody></table>
{% else %}<div class="empty">—</div>{% endif %}
{% endmacro %}

{% if d.groups %}
<section><h2>🔥 族群熱力區（前段班 = 個股 VCP 評分 +1）</h2>
<div class="hot">
<div class="hotcol"><p class="hoth">🏭 產業（證交所）· 強度排名</p>{{ group_table(d.groups.industries) }}</div>
<div class="hotcol"><p class="hoth">🧩 主題（產業鏈）· 強度排名</p>{{ group_table(d.groups.themes) }}</div>
</div></section>
{% endif %}

<section><h2>🟢 LEADERS · 主攻（全門檻通過）</h2>
{% if d.LEADERS %}{{ trade_table(d.LEADERS) }}{% else %}<div class="empty">今日無 LEADERS（紅盤常見，系統不勉強選股）。</div>{% endif %}</section>

{% macro ready_table(rows) %}
{% if rows %}<table><thead><tr>
<th>代號</th><th>名稱</th><th>產業</th><th>RS</th><th>收盤</th><th>距52高</th><th>樞紐寬</th><th>旗標</th></tr></thead><tbody>
{% for x in rows %}<tr>
<td>{{ x.stock_id }}</td><td>{{ x.name }}</td><td class="l muted">{{ x.industry }}</td>
<td>{{ x.rs_rating }}</td><td>{{ x.close }}</td><td>{{ '%+.1f%%'|format(x.dist_52w_high*100) }}</td>
<td>{{ '%.1f%%'|format(x.pivot_width*100) }}</td>
<td class="l flag">{% if x.rs_line_new_high %}RS線新高 {% endif %}{% if x.group_top %}<span class="tag to">族群#{{ x.group_rank }}</span>{% endif %}</td>
</tr>{% endfor %}</tbody></table>
{% else %}<div class="empty">—</div>{% endif %}
{% endmacro %}

<section><h2>🟡 READY · 觀察（依成熟度分三組，每組內最貼近突破者在前）</h2>
{% if d.READY %}
{% set r1 = d.READY|selectattr('ready_tier','equalto',1)|sort(attribute='dist_52w_high',reverse=true)|list %}
{% set r2 = d.READY|selectattr('ready_tier','equalto',2)|sort(attribute='dist_52w_high',reverse=true)|list %}
{% set r3 = d.READY|selectattr('ready_tier','equalto',3)|sort(attribute='dist_52w_high',reverse=true)|list %}
<p class="hoth">🔥 即將收緊（樞紐 ≤18%）· {{ r1|length }} 檔 — 每天盯，隨時可能升 LEADER</p>{{ ready_table(r1) }}
<p class="hoth" style="margin-top:16px">📈 發展中（樞紐 18–25%）· {{ r2|length }} 檔 — 基底成形中</p>{{ ready_table(r2) }}
<p class="hoth" style="margin-top:16px">👀 早期觀察（樞紐 &gt;25%）· {{ r3|length }} 檔 — 強股留底、還早</p>{{ ready_table(r3) }}
{% else %}<div class="empty">今日無 READY。</div>{% endif %}</section>

<section><h2>🚀 BREAKOUT · 當日突破</h2>
{% if d.BREAKOUT %}{{ trade_table(d.BREAKOUT) }}{% else %}<div class="empty">今日無突破。</div>{% endif %}</section>

<div class="foot">VCP 選股大腦 L1｜緊度派 gate（近52週高≤25% + 10日樞紐&lt;10%）｜資料 FinMind｜
進場=樞紐高、停損=樞紐低/末段低、風報比≥3:1、單筆≤總資金10%（A-1 紅燈降倉）。本頁為研究輔助，非投資建議。</div>
</div></body></html>""")


def build(json_path: str = C.OUTPUT_JSON, html_path: str = C.OUTPUT_HTML) -> None:
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    html = TEMPLATE.render(d=d)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"→ {html_path}")


if __name__ == "__main__":
    build(*(sys.argv[1:3] if len(sys.argv) > 1 else []))
