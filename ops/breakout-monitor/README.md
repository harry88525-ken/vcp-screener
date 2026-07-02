# VCP 盤中突破監控（vcp-breakout-monitor）

盤中（台北 09:00–13:30）每分鐘掃「🎯 待突破」名單，某檔**站上買點**就即時 Telegram 推播。
解決「收盤才看到突破、隔天 price 跑掉」。

## 運作
```
CF Worker（每分鐘，開盤時段）
 ├─ 讀 leaders.json（GitHub Pages 公開）→ 挑 breakout_status=="待突破"，帶買點 pivot_high、量門檻 avg_vol×1.4
 ├─ 讀 Fugle 快照 TSE+OTC（2 次呼叫）→ 每檔即時 lastPrice、tradeVolume(張)
 ├─ 現價 ≥ 買點 → 命中（每檔每天只叫一次，KV 去重）
 └─ Telegram 推播（附量能進度：量已到位✅ / 量僅 X%⚠️）
```
- **只讀公開資料 + Fugle，不碰 VCP 掃描 pipeline。**
- 觸發＝**價格站上買點就叫**（量能只當訊息裡的參考，不擋通知；因盤中量本來累積不到全日門檻）。
  要改成「量也到 1.4× 才叫」＝改 worker.js `run()` 裡的判斷式。

## 部署
需要 Node + wrangler，登入 Cloudflare（token `vcp-worker-deploy` 在 🔐 金鑰保險箱）。
```bash
cd ops/breakout-monitor
npx wrangler kv namespace create FIRED     # 把回傳 id 貼進 wrangler.toml
npx wrangler secret put FUGLE_API_KEY      # 保險箱：Fugle X-API-KEY
npx wrangler secret put TELEGRAM_BOT_TOKEN # BotFather 給的 token
npx wrangler secret put TELEGRAM_CHAT_ID   # 你的 chat id
npx wrangler secret put TRIGGER_KEY        # 隨便一組字串，手動測試用
npx wrangler deploy
```

## 手動測試（不等開盤）
```
https://vcp-breakout-monitor.<subdomain>.workers.dev/?key=<TRIGGER_KEY>&force=1
```
回傳 JSON：掃了幾檔 targets、fire 了哪些。force=1 略過交易時段閘門。

## 調參（worker.js 頂部）
- `VOL_MULT` 量門檻倍數（預設 1.4）
- `MAX_TARGETS` 最多盯幾檔（預設 20，依距買點排序）
- `SESSION_START/END` 交易時段（台北分鐘）
