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


def gather(client: FinMindClient, stock_id: str, as_of: str | None = None) -> dict:
    """拉全套深度資料，回結構化 digest（L3 輸入）。"""
    end = as_of or dt.date.today().isoformat()
    out: dict = {"stock_id": stock_id, "as_of": end, "l1l2": _l1l2_context(stock_id)}

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
        f"L1/L2 技術面/族群：{json.dumps(ctx, ensure_ascii=False)}",
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


SYSTEM = """你是台股 VCP 深度研究分析師。依下列個股的技術面(L1)、族群(L2)、深度財報與籌碼，產出**繁體中文**結構化研究報告：
結論先行(一句信心判斷) → 基本面(EPS/營收/雙率/現金流/股利趨勢) → 技術面 → 籌碼(法人動向，特別留意背離) → 族群定位 → 進場/停損/倉位 → 一句話判斷。
重點：把訊號變成下單信心，明確點出 L1/L2 看不到的東西（如形態好但法人在賣的背離）。客觀、有立場、不堆術語。"""


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


def render_html(stock_id: str, report_md: str, digest: dict) -> str:
    """把研究報告寫成 docs/analysis/{id}.html（風格對齊選股報告）。回檔案路徑。"""
    os.makedirs(ANALYSIS_DIR, exist_ok=True)
    name = (digest.get("l1l2") or {}).get("name", "")
    body = "".join(f"<p>{ln}</p>" if ln.strip() else "" for ln in report_md.split("\n"))
    html = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>L3 深挖 · {stock_id} {name}</title>
<style>:root{{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--mut:#8b949e;--b:#58a6ff}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,"PingFang TC","Microsoft JhengHei",sans-serif}}
.wrap{{max-width:780px;margin:0 auto;padding:24px}}h1{{font-size:20px}}a{{color:var(--b)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 22px}}
.foot{{color:var(--mut);font-size:11px;margin-top:24px;border-top:1px solid var(--line);padding-top:12px}}</style></head>
<body><div class="wrap"><h1>🔬 L3 深度研究 · {stock_id} {name}</h1>
<div class="card">{body}</div>
<p style="margin-top:18px"><a href="index.html">← 回選股報告</a></p>
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
