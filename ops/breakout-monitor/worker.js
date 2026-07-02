// Cloudflare Worker — VCP 盤中突破監控 + 網頁即時資料代理
// ─────────────────────────────────────────────
// 一魚兩吃：
//   (A) scheduled（Cron，開盤每分鐘）：待突破名單站上買點 → Telegram 推播
//   (B) fetch GET /live：回傳狙擊清單 + 即時價 JSON，給網頁儀表板用（key 藏這，不外露）
//
// 資料來源：
//   1) 公開 leaders.json（GitHub Pages）— pivot_high(買點)/avg_vol/breakout_status
//   2) Fugle 整市場快照（TSE + OTC）— 即時 lastPrice / tradeVolume(張)
//   ⚠️ 只讀公開資料 + Fugle，不碰 VCP 掃描 pipeline。
//
// 機密（wrangler secret，不在本檔、不進 repo）：
//   FUGLE_API_KEY（必要）/ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（推播才要）/ TRIGGER_KEY（手動測試）
// 綁定：KV「FIRED」— 今天這檔已推播（去重）。

const LEADERS_URL = "https://harry88525-ken.github.io/vcp-screener/leaders.json";
const SNAPSHOT = (mkt) =>
  `https://api.fugle.tw/marketdata/v1.0/snapshot/quotes/${mkt}`;

const VOL_MULT = 1.4;
const MAX_TARGETS = 25;
const SESSION_START = 9 * 60;
const SESSION_END = 13 * 60 + 35;

function taipeiNow() {
  const t = new Date(Date.now() + 8 * 3600 * 1000);
  return {
    minutes: t.getUTCHours() * 60 + t.getUTCMinutes(),
    hhmm: `${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`,
    date: `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(t.getUTCDate()).padStart(2, "0")}`,
    dow: t.getUTCDay(),
  };
}

function inSession(now) {
  return now.dow !== 0 && now.dow !== 6 &&
    now.minutes >= SESSION_START && now.minutes <= SESSION_END;
}

async function fetchJSON(url, headers = {}) {
  const res = await fetch(url, { headers, cf: { cacheTtl: 0 } });
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function fetchSnapshotMap(env) {
  const map = new Map();
  for (const mkt of ["TSE", "OTC"]) {
    const snap = await fetchJSON(SNAPSHOT(mkt), { "X-API-KEY": env.FUGLE_API_KEY });
    for (const x of snap.data || []) {
      const price = x.lastPrice ?? x.closePrice;
      if (price == null) continue;
      map.set(x.symbol, { price, volLots: x.tradeVolume ?? 0 });
    }
  }
  return map;
}

// 核心：抓名單 + 即時價，算出每檔狀態（推播與網頁共用）
async function gather(env) {
  const leaders = await fetchJSON(LEADERS_URL);
  const pool = [...(leaders.LEADERS || []), ...(leaders.READY || [])];
  const raw = [];
  for (const r of pool) {
    if (r.breakout_status !== "待突破") continue;
    if (!r.pivot_high || !r.close) continue;
    raw.push({
      id: r.stock_id, name: r.name || r.stock_id, industry: r.industry || "",
      buy: r.pivot_high, close: r.close, stop: r.stop ?? null,
      riskPct: r.risk_pct ?? null, rs: r.rs_rating ?? null,
      volThresholdShares: Math.round((r.avg_vol || 0) * VOL_MULT),
    });
  }
  const snap = await fetchSnapshotMap(env);
  const targets = [];
  for (const t of raw) {
    const q = snap.get(t.id);
    const price = q ? q.price : null;
    const volShares = q ? q.volLots * 1000 : 0;
    const volPct = t.volThresholdShares ? Math.round((volShares / t.volThresholdShares) * 100) : null;
    const distPct = price != null ? (t.buy - price) / price : (t.buy - t.close) / t.close;
    targets.push({
      id: t.id, name: t.name, industry: t.industry, rs: t.rs,
      buy: t.buy, price, distPct,
      volPct, volThresholdLots: Math.round(t.volThresholdShares / 1000),
      stop: t.stop, riskPct: t.riskPct,
      crossed: price != null && price >= t.buy,
    });
  }
  targets.sort((a, b) => Number(b.crossed) - Number(a.crossed) || a.distPct - b.distPct);
  return { as_of: leaders.as_of, generated_at: leaders.generated_at, targets: targets.slice(0, MAX_TARGETS) };
}

async function tg(env, text) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) console.log("telegram send failed", res.status, await res.text());
  return res.ok;
}

async function pushBreakouts(env, force = false) {
  const now = taipeiNow();
  if (!force && !inSession(now)) return { skipped: "off-hours" };
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return { skipped: "no-telegram" };

  const { targets } = await gather(env);
  const fired = [];
  for (const t of targets) {
    if (!t.crossed) continue;
    const dedupKey = `${now.date}:${t.id}`;
    if (env.FIRED && (await env.FIRED.get(dedupKey))) continue;
    const volTag = t.volPct != null && t.volPct >= 100 ? "✅ 量已到位" : `⚠️ 量僅 ${t.volPct ?? "?"}%（留意收盤量）`;
    const msg =
      `🚀 <b>${t.name}（${t.id}）站上買點</b>\n` +
      `現價 <b>${t.price}</b> ≧ 買點 ${t.buy}\n` +
      `量能：${volTag}（門檻 ${t.volThresholdLots} 張）\n` +
      `停損 ${t.stop ?? "-"}　風險 ${t.riskPct != null ? (t.riskPct * 100).toFixed(1) + "%" : "-"}\n` +
      `⏱ ${now.date} 台北 ${now.hhmm}`;
    await tg(env, msg);
    if (env.FIRED) await env.FIRED.put(dedupKey, "1", { expirationTtl: 8 * 3600 });
    fired.push(t.id);
  }
  return { targets: targets.length, fired };
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json; charset=utf-8",
};

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(pushBreakouts(env));
  },
  async fetch(request, env) {
    const url = new URL(request.url);

    // (B) 網頁即時資料：公開 GET /live
    if (url.pathname === "/live") {
      try {
        const data = await gather(env);
        const now = taipeiNow();
        return new Response(JSON.stringify({ ...data, server_time: now.hhmm, in_session: inSession(now) }), { headers: CORS });
      } catch (e) {
        return new Response(JSON.stringify({ error: String(e) }), { status: 502, headers: CORS });
      }
    }

    // 手動測試推播：/?key=<TRIGGER_KEY>&force=1
    if (!env.TRIGGER_KEY || url.searchParams.get("key") !== env.TRIGGER_KEY)
      return new Response("forbidden", { status: 403 });
    const out = await pushBreakouts(env, url.searchParams.get("force") === "1");
    return new Response(JSON.stringify(out, null, 2), { headers: { "Content-Type": "application/json" } });
  },
};
