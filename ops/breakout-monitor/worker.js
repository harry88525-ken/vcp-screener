// Cloudflare Worker — VCP 盤中監控 + 網頁即時資料/基本面代理
// ─────────────────────────────────────────────
//   (A) scheduled（Cron，開盤每分鐘）：即將突破站上買點 → Telegram 推播
//   (B) GET /live：回四類清單（今天突破/即將突破/LEADERS/READY）+ 即時價 JSON
//   (C) GET /fundamentals?id=XXXX：即時抓 FinMind 基本面（EPS/月營收YoY/法人）
//
// 機密（wrangler secret）：FUGLE_API_KEY（必要）/ FINMIND_TOKEN（基本面）/
//   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（推播）/ TRIGGER_KEY（測試）
// 綁定：KV「FIRED」去重。

const LEADERS_URL = "https://harry88525-ken.github.io/vcp-screener/leaders.json";
const SNAPSHOT = (mkt) => `https://api.fugle.tw/marketdata/v1.0/snapshot/quotes/${mkt}`;
const FINMIND = "https://api.finmindtrade.com/api/v4/data";

const VOL_MULT = 1.4;
const SESSION_START = 9 * 60, SESSION_END = 13 * 60 + 35;

function taipeiNow() {
  const t = new Date(Date.now() + 8 * 3600 * 1000);
  return {
    minutes: t.getUTCHours() * 60 + t.getUTCMinutes(),
    hhmm: `${String(t.getUTCHours()).padStart(2,"0")}:${String(t.getUTCMinutes()).padStart(2,"0")}`,
    date: `${t.getUTCFullYear()}-${String(t.getUTCMonth()+1).padStart(2,"0")}-${String(t.getUTCDate()).padStart(2,"0")}`,
    dow: t.getUTCDay(),
  };
}
const inSession = (n) => n.dow !== 0 && n.dow !== 6 && n.minutes >= SESSION_START && n.minutes <= SESSION_END;

async function fetchJSON(url, headers = {}) {
  const res = await fetch(url, { headers, cf: { cacheTtl: 0 } });
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function snapshotMap(env) {
  const map = new Map();
  for (const mkt of ["TSE", "OTC"]) {
    const snap = await fetchJSON(SNAPSHOT(mkt), { "X-API-KEY": env.FUGLE_API_KEY });
    for (const x of snap.data || []) {
      const price = x.lastPrice ?? x.closePrice;
      if (price != null) map.set(x.symbol, { price, volLots: x.tradeVolume ?? 0 });
    }
  }
  return map;
}

// 把一筆 leaders.json 記錄 + 即時價 → 前端用的物件
function enrich(r, snap, detailed) {
  const q = snap.get(r.stock_id);
  const price = q ? q.price : null;
  const volShares = q ? q.volLots * 1000 : 0;
  const volTh = Math.round((r.avg_vol || 0) * VOL_MULT);
  const distPct = price != null ? (r.pivot_high - price) / price : (r.pivot_high - r.close) / r.close;
  const o = {
    id: r.stock_id, name: r.name || r.stock_id, industry: r.industry || "",
    rs: r.rs_rating ?? null, grade: r.grade || "",
    buy: r.pivot_high, close: r.close, price, distPct,
    volPct: volTh ? Math.round((volShares / volTh) * 100) : null,
    volThresholdLots: Math.round(volTh / 1000),
    stop: r.stop ?? null, riskPct: r.risk_pct ?? null,
    rewardRisk: r.reward_risk ?? null,
    crossed: price != null && price >= r.pivot_high,
  };
  if (detailed) Object.assign(o, {
    dist52wHigh: r.dist_52w_high ?? null, pivotWidth: r.pivot_width ?? null,
    isVcp: r.is_vcp ?? null, seqClean: r.seq_clean ?? null,
    volContraction: r.vol_contraction ?? null, contractions: r.contractions ?? null,
    rsLineNewHigh: r.rs_line_new_high ?? null,
    epsYoy: r.eps_yoy ?? null, roe: r.roe ?? null, rev3mYoy: r.rev_3m_yoy ?? null,
    instNetBuy: r.inst_net_buy ?? null, trustStreak: r.trust_streak ?? null,
    fundamentalOk: r.fundamental_ok ?? null,
    groupRank: r.group_rank ?? null, groupTop: r.group_top ?? null, groupReal: r.group_real ?? null,
  });
  return o;
}

async function gather(env) {
  const L = await fetchJSON(LEADERS_URL);
  const snap = await snapshotMap(env);
  const leaders = L.LEADERS || [], ready = L.READY || [], breakout = L.BREAKOUT || [];
  const isWait = (r) => r.breakout_status === "待突破";

  const approaching = [...leaders, ...ready].filter(r => isWait(r) && r.pivot_high && r.close)
    .map(r => enrich(r, snap, false)).sort((a,b)=>Number(b.crossed)-Number(a.crossed)||a.distPct-b.distPct).slice(0,30);
  const leadersOut = leaders.map(r => enrich(r, snap, true))
    .sort((a,b)=>Number(b.crossed)-Number(a.crossed)||a.distPct-b.distPct);
  const breakoutOut = breakout.map(r => enrich(r, snap, false)).sort((a,b)=>(b.rs||0)-(a.rs||0));
  const readyOut = ready.map(r => enrich(r, snap, false))
    .sort((a,b)=>Number(b.crossed)-Number(a.crossed)||a.distPct-b.distPct);

  return { as_of: L.as_of, generated_at: L.generated_at,
    market: L.market || null,
    counts: { breakout: breakoutOut.length, approaching: approaching.length, leaders: leadersOut.length, ready: readyOut.length },
    breakout: breakoutOut, approaching, leaders: leadersOut, ready: readyOut };
}

// 即時抓 FinMind 基本面（點代號才呼叫）
async function fmData(env, dataset, id, start) {
  const u = `${FINMIND}?dataset=${dataset}&data_id=${id}&start_date=${start}&token=${encodeURIComponent(env.FINMIND_TOKEN)}`;
  try { const j = await fetchJSON(u); return j.data || []; } catch { return []; }
}
async function fundamentals(env, id) {
  const rev = await fmData(env, "TaiwanStockMonthRevenue", id, "2024-01-01");
  let revOut = null;
  if (rev.length) {
    rev.sort((a,b)=>(a.revenue_year-b.revenue_year)||(a.revenue_month-b.revenue_month));
    const last = rev[rev.length-1];
    const prior = rev.find(r=>r.revenue_year===last.revenue_year-1 && r.revenue_month===last.revenue_month);
    revOut = { ym: `${last.revenue_year}/${last.revenue_month}`, yi: +(last.revenue/1e8).toFixed(2),
      yoy: prior ? +((last.revenue/prior.revenue-1)*100).toFixed(1) : null };
  }
  const fs = await fmData(env, "TaiwanStockFinancialStatements", id, "2024-01-01");
  const eps = fs.filter(x=>x.type==="EPS").sort((a,b)=>a.date<b.date?-1:1);
  const epsOut = eps.length ? { date: eps[eps.length-1].date, value: eps[eps.length-1].value } : null;
  const inst = await fmData(env, "TaiwanStockInstitutionalInvestorsBuySell", id, "2026-05-01");
  let instOut = null;
  if (inst.length) {
    const days = [...new Set(inst.map(x=>x.date))].sort().slice(-5);
    const net = inst.filter(x=>days.includes(x.date)).reduce((s,x)=>s+(x.buy-x.sell),0);
    instOut = { lots: Math.round(net/1000), days: days.length };
  }
  return { id, revenue: revOut, eps: epsOut, inst5: instOut };
}

async function tg(env, text) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) console.log("tg fail", res.status, await res.text());
}
async function pushBreakouts(env, force = false) {
  const now = taipeiNow();
  if (!force && !inSession(now)) return { skipped: "off-hours" };
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return { skipped: "no-telegram" };
  const { approaching } = await gather(env);
  const fired = [];
  for (const t of approaching) {
    if (!t.crossed) continue;
    const k = `${now.date}:${t.id}`;
    if (env.FIRED && (await env.FIRED.get(k))) continue;
    const vTag = t.volPct != null && t.volPct >= 100 ? "✅ 量已到位" : `⚠️ 量僅 ${t.volPct ?? "?"}%`;
    await tg(env, `🚀 <b>${t.name}（${t.id}）站上買點</b>\n現價 <b>${t.price}</b> ≧ 買點 ${t.buy}\n量能：${vTag}（門檻 ${t.volThresholdLots} 張）\n停損 ${t.stop ?? "-"}　風險 ${t.riskPct != null ? (t.riskPct*100).toFixed(1)+"%" : "-"}\n⏱ ${now.date} 台北 ${now.hhmm}`);
    if (env.FIRED) await env.FIRED.put(k, "1", { expirationTtl: 8*3600 });
    fired.push(t.id);
  }
  return { fired };
}

const CORS = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json; charset=utf-8" };

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(pushBreakouts(env)); },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/live") {
      try {
        const d = await gather(env); const now = taipeiNow();
        return new Response(JSON.stringify({ ...d, server_time: now.hhmm, in_session: inSession(now) }), { headers: CORS });
      } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { status: 502, headers: CORS }); }
    }
    if (url.pathname === "/fundamentals") {
      const id = url.searchParams.get("id");
      if (!id || !/^\d{4,6}$/.test(id)) return new Response(JSON.stringify({ error: "bad id" }), { status: 400, headers: CORS });
      try { return new Response(JSON.stringify(await fundamentals(env, id)), { headers: CORS }); }
      catch (e) { return new Response(JSON.stringify({ error: String(e) }), { status: 502, headers: CORS }); }
    }
    if (!env.TRIGGER_KEY || url.searchParams.get("key") !== env.TRIGGER_KEY) return new Response("forbidden", { status: 403 });
    return new Response(JSON.stringify(await pushBreakouts(env, url.searchParams.get("force") === "1"), null, 2), { headers: { "Content-Type": "application/json" } });
  },
};
