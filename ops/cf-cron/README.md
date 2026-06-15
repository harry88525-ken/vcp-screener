# VCP L1 準時觸發器（Cloudflare Worker + Cron）

GitHub 內建排程（`schedule:` cron）會被降級延遲（曾延遲 4 小時）。
此 Worker 用 Cloudflare Cron Trigger 準時打 `workflow_dispatch`（不排隊）觸發 L1 每日掃描。

- **引擎仍在 GitHub Actions**（`daily.yml`）；這個 Worker 只是「準時的鬧鐘」。
- **排程**：07:00 UTC = 15:00 台北，週一~五（見 `wrangler.toml` 的 `[triggers]`）。
- **GitHub cron 保留當 fallback**（電腦/CF 萬一都失效時，晚一點還是會跑）。

## 部署
```bash
cd ops/cf-cron
export CLOUDFLARE_API_TOKEN=<Cloudflare token，Workers Scripts:Edit>
export CLOUDFLARE_ACCOUNT_ID=<account id>
npx wrangler deploy
npx wrangler secret put GH_PAT     # 貼入 GitHub PAT（actions:write on vcp-screener）
```

## 測試
- 手動打 Worker URL（`https://vcp-l1-cron.<subdomain>.workers.dev`）會立刻 dispatch 一次，回 `dispatched ✅`。
- 到 GitHub Actions 看是否多一筆 `workflow_dispatch` 的 run。
