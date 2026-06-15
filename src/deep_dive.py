# -*- coding: utf-8 -*-
"""
L3 基本面深挖（研究層）
======================
把 L1/L2 篩出的標的（LEADER 自動 / READY 按需）做深度研究，產出研究報告頁。
分進程：研究層「貴/按需」，與便宜的 L1/L2 運算層分開。

流程：gather（拉深度資料→結構化 digest）→ to_prompt（組 Opus 輸入）
     → synthesize（Opus 4.8 產報告，需 ANTHROPIC_API_KEY；無 key 回 None，由 Claude session 按需代跑）
     → render_html（寫 docs/analysis/{id}.html）

重用機制：報告頁存在且新鮮（< L3_REUSE_DAYS 天）就跳過，省 token（一檔連當一週 LEADER 只生 1 次）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import pandas as pd

import config as C
from src.finmind_client import FinMindClient

ANALYSIS_DIR = os.path.join("docs", "analysis")
L3_REUSE_DAYS = 7
L3_MODEL = "claude-opus-4-8"


def _recent(end: str, days: int) -> str:
    return (dt.date.fromisoformat(end) - dt.timedelta(days=days)).isoformat()


def _l1l2_context(stock_id: str) -> dict | None:
    """從最新 leaders.json 撈這檔的 L1/L2 紀錄（技術面/族群脈絡）。"""
    try:
        d = json.load(open(C.OUTPUT_JSON, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    keys = ("name", "industry", "rs_rating", "grade", "close", "dist_52w_high",
            "pivot_width", "pivot_high", "stop", "risk_pct", "reward_risk",
            "group_rank", "group_real", "rs_line_new_high", "inst_net_buy", "trust_streak")
    for bucket in ("LEADERS", "READY", "BREAKOUT"):
        for x in d.get(bucket, []):
            if x["stock_id"] == stock_id:
                return {"bucket": bucket, **{k: x.get(k) for k in keys}}
    return None


def _group_context(industry: str | None) -> dict | None:
    """從 groups.json 撈這檔所屬產業的族群定位（排名/動能/廣度/一致性/前段班 peer）。"""
    if not industry:
        return None
    try:
        g = json.load(open(C.OUTPUT_GROUPS_JSON, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return next((s for s in g.get("industries", []) if s["name"] == industry), None)


def _trade_plan(ctx: dict | None) -> dict | None:
    """從 L1 脈絡算完整進場數字：進場(樞紐高)/停損/目標(量測)/風報比 R:R。"""
    if not ctx:
        return None
    entry, stop = ctx.get("pivot_high"), ctx.get("stop")
    if not entry or not stop or entry <= stop:
        return None
    target = round(entry * (1 + C.L3_TARGET_MEASURED_MOVE), 1)
    rr = round((target - entry) / (entry - stop), 2)
    return {"entry": entry, "stop": stop, "target": target, "reward_risk": rr,
            "risk_pct": round((entry - stop) / entry, 4),
            "target_basis": f"樞紐高 ×{1 + C.L3_TARGET_MEASURED_MOVE:.2f} 量測"}


def gather(client: FinMindClient, stock_id: str, as_of: str | None = None) -> dict:
    """拉全套深度資料，回結構化 digest（L3 輸入）。"""
    end = as_of or dt.date.today().isoformat()
    ctx = _l1l2_context(stock_id)
    out: dict = {"stock_id": stock_id, "as_of": end, "l1l2": ctx}
    out["group"] = _group_context((ctx or {}).get("industry"))
    out["trade_plan"] = _trade_plan(ctx)
    try:
        pp = client.per_pbr(stock_id, _recent(end, 400), end)
        if not pp.empty:
            pp = pp.sort_values("date")
            last = pp.iloc[-1]
            per = pp["PER"].dropna()
            out["valuation"] = {
                "per": float(last.get("PER")) if "PER" in pp else None,
                "pbr": float(last.get("PBR")) if "PBR" in pp else None,
                "dividend_yield": float(last.get("dividend_yield")) if "dividend_yield" in pp else None,
                "per_1y_low": round(float(per.min()), 1) if not per.empty else None,
                "per_1y_high": round(float(per.max()), 1) if not per.empty else None,
            }
    except Exception:
        pass

    mr = client.month_revenue(stock_id, _recent(end, 560), end)
    if not mr.empty:
        mr = mr.sort_values("date").tail(15)
        out["revenue_monthly"] = [(int(r["revenue_year"]), int(r["revenue_month"]), float(r["revenue"]))
                                  for _, r in mr.iterrows()]

    fin = client.financials(stock_id, _recent(end, 1200), end)

    def qseries(t):
        if fin.empty:
            return []
        s = fin[fin["type"] == t].sort_values("date")
        return [(d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(s["date"], s["value"])][-8:]

    out["eps"] = qseries("EPS")
    out["revenue_q"] = qseries("Revenue")
    out["gross_profit"] = qseries("GrossProfit")
    out["op_income"] = qseries("OperatingIncome")
    out["net_income"] = qseries("IncomeAfterTaxes")

    try:
        cf = client.cashflow(stock_id, _recent(end, 500), end)
        if not cf.empty:
            op = cf[cf["type"] == "CashReceivedThroughOperations"].sort_values("date")
            out["op_cashflow"] = [(d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(op["date"], op["value"])][-4:]
    except Exception:
        pass

    try:
        dv = client.dividend(stock_id, _recent(end, 800), end)
        if not dv.empty and "CashEarningsDistribution" in dv.columns:
            dv = dv.sort_values("date").tail(6)
            out["dividend"] = [(str(r.get("year")), float(r.get("CashEarningsDistribution") or 0)) for _, r in dv.iterrows()]
    except Exception:
        pass

    inst = client.institutional(stock_id, _recent(end, 25), end)
    if not inst.empty:
        out["inst"] = [(r["date"].strftime("%Y-%m-%d"), float(r["net_total"])) for _, r in
                       inst.sort_values("date").tail(10).iterrows()]
    return out


def to_prompt(digest: dict) -> str:
    """把 digest 組成 Opus 4.8 的研究輸入。"""
    ctx = digest.get("l1l2") or {}
    name = ctx.get("name", "")
    lines = [
        f"標的：{digest['stock_id']} {name}（as-of {digest['as_of']}）",
        f"L1 技術面：{json.dumps(ctx, ensure_ascii=False)}",
        f"L2 族群定位(所屬產業排名/動能1月3月6月/廣度/一致性vcp_count/前段班peer top_members)：{json.dumps(digest.get('group'), ensure_ascii=False)}",
        f"估值(PER/PBR/殖利率/PER近1年高低)：{json.dumps(digest.get('valuation'), ensure_ascii=False)}",
        f"完整進場數字(進場樞紐高/停損/目標量測/風報比R:R)：{json.dumps(digest.get('trade_plan'), ensure_ascii=False)}",
        f"月營收(年,月,值千元)：{digest.get('revenue_monthly')}",
        f"季 EPS：{digest.get('eps')}",
        f"季營收：{digest.get('revenue_q')}",
        f"季毛利：{digest.get('gross_profit')}",
        f"季營業利益：{digest.get('op_income')}",
        f"季稅後淨利：{digest.get('net_income')}",
        f"營運現金流：{digest.get('op_cashflow')}",
        f"股利(年度,現金/股)：{digest.get('dividend')}",
        f"三大法人近10日淨買賣(日期,合計)：{digest.get('inst')}",
    ]
    return "\n".join(lines)


SYSTEM = """你是台股 VCP 深度研究分析師。依個股的技術面(L1)、族群定位(L2)、估值、深度財報、籌碼，產出**繁體中文**結構化研究報告，目的=讓投資人做出最優判斷。依序：

【結論先行】一句信心判斷（明確：可動手 / 觀察 / 跳過）+ 信心等級。
【族群定位】★最重要★ 回答「這是不是這場賽馬裡最好的馬、這場賽值不值得跑」：①這檔在所屬產業排第幾、是領頭還是跟隨 ②板塊動能(1月/3月/6月)→ 同步上漲嗎、剛啟動還是已續航 ③廣度+一致性(真族群 vs 孤狼) ④同板塊最強 peer 比較(要這主題、最強的是這檔還是別檔？)。
【基本面】EPS/營收/雙率/現金流/股利趨勢 + 估值(PER/PBR 相對近1年高低=便宜還貴)。
【籌碼】法人動向，特別點出背離(如形態好但法人在賣)。
【完整進場計畫】進場(樞紐高)/停損/目標/風報比 R:R/倉位建議(A-1 燈號調整)。
【催化劑與風險】明列。
【一句話判斷】

重點：把訊號變成下單信心；明確點出 L1/L2 單看不到的東西（族群是否最強、籌碼背離、估值偏貴）。客觀、有立場、不堆術語。"""


def synthesize(prompt: str, model: str = L3_MODEL) -> str | None:
    """呼叫 Opus 4.8 產報告。需 ANTHROPIC_API_KEY；無 key 回 None（按需時由 Claude session 代跑）。"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def is_fresh(stock_id: str, reuse_days: int = L3_REUSE_DAYS) -> bool:
    """重用判斷：報告頁存在且檔齡 < reuse_days 天就重用（省 token）。"""
    p = os.path.join(ANALYSIS_DIR, f"{stock_id}.html")
    if not os.path.exists(p):
        return False
    import time
    return (time.time() - os.path.getmtime(p)) / 86400 < reuse_days


def _md_inline(s: str) -> str:
    import re, html as _h
    s = _h.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _md_table(rows: list) -> str:
    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]
    parsed = [cells(r) for r in rows]
    body = [r for r in parsed if not all((set(c) <= set("-: ")) for c in r)]   # 丟 |---|---| 分隔列
    if not body:
        return ""
    head, rest = body[0], body[1:]
    th = "".join(f"<th>{_md_inline(c)}</th>" for c in head)
    trs = "".join("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>" for r in rest)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def _md_to_html(md: str) -> str:
    """輕量 Markdown→HTML（逐行；支援 #/##/###、---、- 清單、| 表格、**粗體**、`code`）。
    逐行設計＝不依賴空行分段，對 L3 報告「標題/清單/表格各自成行」結構穩定。"""
    import re
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1; continue
        if s.startswith("### "):
            out.append(f"<h3>{_md_inline(s[4:])}</h3>"); i += 1
        elif s.startswith("## "):
            out.append(f"<h2>{_md_inline(s[3:])}</h2>"); i += 1
        elif s.startswith("# "):
            out.append(f'<p class="lead">{_md_inline(s[2:])}</p>'); i += 1
        elif len(s) >= 3 and set(s) <= set("-"):
            out.append("<hr>"); i += 1
        elif s.startswith("|") and s.count("|") >= 2:
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip()); i += 1
            out.append(_md_table(rows))
        elif re.match(r"^[-*]\s+", s):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i].strip())); i += 1
            out.append("<ul>" + "".join(f"<li>{_md_inline(it)}</li>" for it in items) + "</ul>")
        else:
            out.append(f"<p>{_md_inline(s)}</p>"); i += 1
    return "\n".join(out)


def render_html(stock_id: str, report_md: str, digest: dict) -> str:
    """把研究報告寫成 docs/analysis/{id}.html（Markdown 正確渲染＋美觀深色排版）。回檔案路徑。"""
    os.makedirs(ANALYSIS_DIR, exist_ok=True)
    name = (digest.get("l1l2") or {}).get("name", "")
    body = _md_to_html(report_md)
    html = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>L3 深挖 · {stock_id} {name}</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--mut:#8b949e;--b:#58a6ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.85 -apple-system,"Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:24px 20px 64px}}
h1{{font-size:21px;margin:0 0 18px}}
a{{color:#7ba3c4;text-decoration:none}}a:hover{{text-decoration:underline}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 24px 22px}}
.card .lead{{color:var(--mut);font-size:14px;margin:16px 0 4px}}
.card h2{{font-size:16px;margin:28px 0 12px;padding:9px 13px;background:#11161d;border-left:3px solid var(--b);border-radius:6px;color:#cfe2ff}}
.card h3{{font-size:14px;color:var(--mut);margin:18px 0 6px}}
.card p{{margin:11px 0}}
.card strong{{color:#fff;font-weight:700}}
.card code{{background:#0d1117;border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:13px}}
.card ul{{margin:10px 0;padding:0;list-style:none}}
.card li{{margin:8px 0;padding-left:18px;position:relative}}
.card li::before{{content:"▍";color:var(--b);position:absolute;left:0;top:3px;font-size:11px}}
.card hr{{border:none;border-top:1px solid var(--line);margin:20px 0}}
.card table{{width:100%;border-collapse:collapse;margin:14px 0;font-size:14px}}
.card th,.card td{{padding:8px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
.card th{{color:var(--mut);background:#11161d;font-weight:600}}
.card tr:last-child td{{border-bottom:none}}
.foot{{color:var(--mut);font-size:11px;margin-top:24px;border-top:1px solid var(--line);padding-top:12px}}
</style></head>
<body><div class="wrap"><h1>🔬 L3 深度研究 · {stock_id} {name}</h1>
<div class="card">{body}</div>
<p style="margin-top:18px"><a href="../index.html">← 回選股報告</a></p>
<div class="foot">L3 研究層 · {L3_MODEL} · as-of {digest['as_of']} · 研究輔助非投資建議</div></div></body></html>"""
    p = os.path.join(ANALYSIS_DIR, f"{stock_id}.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    return p


def run_one(client: FinMindClient, stock_id: str, as_of: str | None = None, force: bool = False) -> dict:
    """單檔深挖端到端。回 {stock_id, status, path?}。status: reused/generated/no_key。"""
    if not force and is_fresh(stock_id):
        return {"stock_id": stock_id, "status": "reused"}
    digest = gather(client, stock_id, as_of)
    prompt = to_prompt(digest)
    report = synthesize(prompt)
    if report is None:
        return {"stock_id": stock_id, "status": "no_key", "prompt": prompt}  # 按需時印出 prompt 供 session 代跑
    path = render_html(stock_id, report, digest)
    return {"stock_id": stock_id, "status": "generated", "path": path}


def main():
    ap = argparse.ArgumentParser(description="L3 基本面深挖")
    ap.add_argument("stock_id")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--digest", action="store_true", help="只印 digest/prompt，不呼叫 Opus")
    args = ap.parse_args()
    client = FinMindClient()
    if args.digest:
        print(to_prompt(gather(client, args.stock_id, args.as_of)))
        return
    r = run_one(client, args.stock_id, args.as_of, args.force)
    print(json.dumps({k: v for k, v in r.items() if k != "prompt"}, ensure_ascii=False))
    if r["status"] == "no_key":
        print("\n[無 ANTHROPIC_API_KEY → 以下 prompt 供 Claude session 按需代跑]\n")
        print(r["prompt"])


if __name__ == "__main__":
    main()
