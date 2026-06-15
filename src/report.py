# -*- coding: utf-8 -*-
"""
報告產生器：docs/leaders.json → docs/index.html
固定樣式，只換內容（KEN 偏好）。運算與呈現分離——本檔只讀 JSON，不算任何指標。
"""
from __future__ import annotations

import glob
import json
import os
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
a{color:#7ba3c4;text-decoration:none}a:hover{text-decoration:underline}
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
.q-ok{color:var(--a);font-weight:700}.q-no{color:var(--mut)}
.foot{color:var(--mut);font-size:11px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}
.sidebar{position:fixed;left:14px;top:88px;width:176px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:12px;max-height:82vh;overflow:auto}
.sidebar h3{font-size:12px;margin:0 0 8px;color:var(--fg)}
.sidebar ol{margin:0;padding-left:22px}.sidebar li{margin-bottom:6px;line-height:1.3}
.sidebar .v{color:var(--mut);font-size:11px}
.sidebar .sbnote{color:var(--mut);font-size:10px;margin-top:8px;border-top:1px solid var(--line);padding-top:6px}
@media(max-width:1480px){.sidebar{display:none}}
</style></head><body>
{% if d.top_volume %}<div class="sidebar"><h3>🔊 昨日成交值 Top{{ d.top_volume|length }}</h3>
<ol>{% for x in d.top_volume %}<li><b>{{ x.stock_id }}</b> {{ x.name }}<br><span class="v">{{ '%.0f'|format((x.turnover_last or 0)/100000000) }} 億　收 {{ x.close }}</span></li>{% endfor %}</ol>
<div class="sbnote">當日最大量能（排除金融/ETF）</div></div>{% endif %}
<div class="wrap">
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

{% macro trade_table(rows) %}
<table><thead><tr>
<th>代號</th><th>名稱</th><th>產業</th><th>RS</th><th>評分</th><th>收盤</th><th>距52高</th>
<th>樞紐(買點)</th><th>停損</th><th>風險%</th><th>R:R</th><th>狀態</th><th>加分</th></tr></thead><tbody>
{% for x in rows %}<tr>
<td>{% if x.stock_id in analyzed %}<a href="analysis/{{ x.stock_id }}.html">{{ x.stock_id }} 🔬</a>{% else %}{{ x.stock_id }}{% endif %}</td><td>{{ x.name }}</td><td class="l muted">{{ x.industry }}</td>
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

{% macro snipe_table(rows) %}
{% if rows %}<table><thead><tr>
<th>代號</th><th>名稱</th><th>產業</th><th>級別</th><th>判定</th><th>收盤</th><th>買點(突破價)</th><th>距突破</th><th>量門檻(張)</th><th>停損</th><th>風險%</th></tr></thead><tbody>
{% for x in rows %}<tr>
<td>{% if x.stock_id in analyzed %}<a href="analysis/{{ x.stock_id }}.html">{{ x.stock_id }} 🔬</a>{% else %}{{ x.stock_id }}{% endif %}</td><td>{{ x.name }}</td><td class="l muted">{{ x.industry }}</td>
<td class="l">{{ x._bucket }}</td><td class="l">{% if x._qualified %}<span class="q-ok">✅合格</span>{% else %}<span class="q-no">⚪觀察</span>{% endif %}</td><td>{{ x.close }}</td><td><b style="color:var(--b)">{{ x.pivot_high }}</b></td>
<td>{{ '%+.1f%%'|format(x.dist_to_pivot*100) }}</td><td>{{ x._vol_lots }}</td>
<td>{{ x.stop }}</td><td>{{ '%.1f%%'|format(x.risk_pct*100) }}</td>
</tr>{% endfor %}</tbody></table>
{% else %}<div class="empty">今日無貼近突破的狙擊標的（樞紐都還沒收緊到位）。</div>{% endif %}
{% endmacro %}

<section><h2>🎯 明日突破狙擊清單（最貼近買點者在前）</h2>
<p class="hoth" style="margin:0 0 10px">隔天開盤只盯這幾檔：收盤站上「買點」且當日量 ≥「量門檻」才進場。量門檻＝50 日均量 ×1.4。<b class="q-ok">✅合格</b>＝樞紐&lt;10%＋風險≤8%＋測幅R:R≥3（乾淨設定、可下手）；<span class="q-no">⚪觀察</span>＝貼著高但基底太鬆、別追。{{ watch|length }} 檔。</p>
{{ snipe_table(watch) }}</section>

<section><h2>🟢 LEADERS · 主攻（全門檻通過）</h2>
{% if d.LEADERS %}{{ trade_table(d.LEADERS) }}{% else %}<div class="empty">今日無 LEADERS（紅盤常見，系統不勉強選股）。</div>{% endif %}</section>

<section><h2>🚀 BREAKOUT · 當日突破</h2>
{% if d.BREAKOUT %}{{ trade_table(d.BREAKOUT) }}{% else %}<div class="empty">今日無突破。</div>{% endif %}</section>

{% macro ready_table(rows) %}
{% if rows %}<table><thead><tr>
<th>代號</th><th>名稱</th><th>產業</th><th>RS</th><th>收盤</th><th>距52高</th><th>樞紐寬</th><th>旗標</th></tr></thead><tbody>
{% for x in rows %}<tr>
<td>{% if x.stock_id in analyzed %}<a href="analysis/{{ x.stock_id }}.html">{{ x.stock_id }} 🔬</a>{% else %}{{ x.stock_id }}{% endif %}</td><td>{{ x.name }}</td><td class="l muted">{{ x.industry }}</td>
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

{% if d.groups %}
<section><h2>🔥 族群熱力區（前段班 = 個股 VCP 評分 +1）</h2>
<div class="hot">
<div class="hotcol"><p class="hoth">🏭 產業（證交所）· 強度排名</p>{{ group_table(d.groups.industries) }}</div>
<div class="hotcol"><p class="hoth">🧩 主題（產業鏈）· 強度排名</p>{{ group_table(d.groups.themes) }}</div>
</div></section>
{% endif %}

<div class="foot">VCP 選股大腦 L1｜緊度派 gate（近52週高≤25% + 10日樞紐&lt;10%）｜資料 FinMind｜
進場=樞紐高、停損=樞紐低/末段低、風報比≥3:1、單筆≤總資金10%（A-1 紅燈降倉）。本頁為研究輔助，非投資建議。</div>
</div></body></html>""")


def _watchlist(d: dict) -> list:
    """🎯 明日突破狙擊：LEADERS + READY tier1 中『待突破』者，依距買點由近到遠。
    買點=樞紐高、量門檻=50日均量×1.4(張)。avg_vol 缺時退用 avg_turnover/close 近似。"""
    out = []
    for x in d.get("LEADERS", []):
        if x.get("breakout_status") == "待突破":
            out.append({**x, "_bucket": "LEADER"})
    for x in d.get("READY", []):
        if x.get("ready_tier") == 1 and x.get("breakout_status") == "待突破":
            out.append({**x, "_bucket": "READY①"})
    for x in out:
        ph, cl = x.get("pivot_high"), x.get("close")
        x["dist_to_pivot"] = (ph / cl - 1) if (ph and cl) else None
        av = x.get("avg_vol") or ((x.get("avg_turnover_50") or 0) / cl if cl else 0)
        x["_vol_lots"] = int(round(av * C.BREAKOUT_VOLUME_MULT / 1000))
        # ✅合格＝樞紐<10% + 風險≤8% + 測幅R:R(樞紐高×1.2÷風險)≥3（乾淨 LEADER 級設定）
        pw, rk = x.get("pivot_width"), x.get("risk_pct")
        rr_mm = (C.L3_TARGET_MEASURED_MOVE / rk) if rk and rk > 0 else 0
        x["_qualified"] = bool(pw is not None and pw < C.PIVOT_WIDTH_MAX
                               and rk and rk <= C.RISK_PER_TRADE_MAX and rr_mm >= C.REWARD_RISK_MIN)
    out.sort(key=lambda r: r["dist_to_pivot"] if r["dist_to_pivot"] is not None else 9)
    return out[:12]


def build(json_path: str = C.OUTPUT_JSON, html_path: str = C.OUTPUT_HTML) -> None:
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    adir = os.path.join(os.path.dirname(html_path) or ".", "analysis")
    analyzed = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(adir, "*.html"))}
    html = TEMPLATE.render(d=d, analyzed=analyzed, watch=_watchlist(d))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"→ {html_path}")


if __name__ == "__main__":
    build(*(sys.argv[1:3] if len(sys.argv) > 1 else []))
