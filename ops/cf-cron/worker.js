// Cloudflare Worker — VCP L1 準時觸發器
// ─────────────────────────────────────────────
// 目的：取代 GitHub 內建排程（會被降級延遲），用 Cloudflare Cron Trigger 準時
// 去打 GitHub workflow_dispatch（手動觸發＝正常優先級、不排隊）。
// 機密：GH_PAT 是 wrangler secret（不在本檔、不進 repo）。
// Cron 設定在 wrangler.toml 的 [triggers]（UTC）。

const OWNER = "harry88525-ken";
const REPO = "vcp-screener";
const WORKFLOW = "daily.yml";
const REF = "main";

async function dispatch(env) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "vcp-l1-cron-worker",
    },
    body: JSON.stringify({ ref: REF }),
  });
  const ok = res.status === 204; // GitHub 成功 dispatch 回 204 No Content
  if (!ok) console.log("dispatch failed", res.status, await res.text());
  return ok;
}

export default {
  // 排程觸發（Cron Trigger）
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // 手動驗證用：打 Worker URL?key=<TRIGGER_KEY> 才會觸發（防路人 spam 觸發 L1）
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!env.TRIGGER_KEY || url.searchParams.get("key") !== env.TRIGGER_KEY) {
      return new Response("forbidden", { status: 403 });
    }
    const ok = await dispatch(env);
    return new Response(ok ? "dispatched ✅" : "dispatch failed ❌", {
      status: ok ? 200 : 502,
    });
  },
};
