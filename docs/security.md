# 安全

English: Feishu credentials and the Grok Bot sender key stay in the bridge environment. The allowlist defaults to reject-all. The outbox directory is mode 0700. Logs are JSON and redact those secrets. Run one process.

## 允許名單

`FEISHU_ALLOWED_OPEN_IDS` 是逗號分隔的 `open_id`。空的時候每一則訊息都會被拒絕，只在日誌留下 `sender_open_id`，不會回覆、也不會呼叫 webhook。把 open_id 加進去之後要重啟。

私訊只要發送者在名單上就受理。群組還必須 @ 到機器人。設定了 `FEISHU_BOT_OPEN_ID` 時只認那個 id；沒設定時，除了單獨的 `@_all`，任何具體 @ 都算。請不要申請「讀取群內所有訊息」，除非你同時設定了 bot open_id。

機器人自己的訊息（`sender_type` 不是 `user`）會丟掉，避免迴圈。

## 密鑰

- `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`GROKBOT_WEBHOOK_KEY` 只放在橋接行程的環境或 `.env`。`.env` 建議 `chmod 600`。儲存庫只提交 `.env.example`。
- Grok Bot 的 routine 提示詞拿不到飛書 app secret。它只拿到任務 JSON 與 outbox 路徑。
- 日誌是一行一個 JSON。App Secret 與 sender key（長度至少 8）會被替換成 `***`。不要把日誌等級長期開在 `DEBUG`：lark-oapi 在 DEBUG 可能印出請求細節。
- 訊息內文不寫進 info 日誌。使用者可能把密碼貼進飛書。
- Webhook 不跟隨重新導向，避免 `Authorization` 被帶到別的主機。URL 不允許內嵌帳號密碼。標頭名稱與 scheme 若含換行會直接拒絕。

## Outbox

`deploy/install.sh` 會把 `outbox/` 與 `data/` 設成 `0700`。橋接啟動時也會再設一次。

結果 JSON 可以指名任意絕對路徑當附件。橋接會用飛書身分把那個檔案上傳。因此 outbox 只能由橋接（以及同一使用者身分的 Grok Bot）寫入。其他本地使用者不該有寫入權。

不要把 `.env`、token、cookie 放進回覆或附件。routine 提示詞也這樣要求。

## 單一實例

飛書長連接是叢集模式：多個 client 時，每則事件只進其中一個，而且是隨機的。兩個行程會把對話拆開，卡片與回覆對不上。

橋接對 `BRIDGE_DB_PATH` 旁的鎖檔加排他鎖。第二個行程會結束並在日誌留下 "another bridge instance"。systemd 與 supervisord 範例都是 `numprocs=1` / 單一 service。

## 健康檢查

`python -m bridge.main --check` 會檢查設定、outbox 與資料庫目錄是否可寫。若 `BRIDGE_HEALTH_FILE` 存在且超過 `BRIDGE_HEALTH_STALE_SECONDS`（預設 30 秒）沒更新，結束碼是 1。它不會連去飛書或 webhook。

行程執行時每隔 `BRIDGE_HEALTH_INTERVAL_SECONDS`（預設 10 秒）原子更新健康檔，權限 `0600`。
