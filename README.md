# 飛書 × Grok Bot 橋接

讓飛書裡的人私訊企業自建應用，或在群組 @ 它，把事情交給 Grok Bot 助理 Javis，再在原對話收到進度與結果。

飛書這一側只用官方 SDK `lark-oapi` 的長連接（WebSocket）。沒有飛書 HTTP callback，也不需要公開的進站網址。任務用 HTTPS POST 送到 Grok Bot routine 的 webhook。Grok Bot 不能持有飛書 App Secret，所以它把結果寫進同一台機器上的 outbox，由這個橋接代為送到飛書。

## 設定（15 步）

1. 在飛書開發者後台建立**企业自建应用**，開啟機器人，從「憑證與基礎信息」複製 App ID 與 App Secret。細節在 [docs/feishu-app-setup.md](docs/feishu-app-setup.md)。
2. 開通應用身份權限：`im:message.p2p_msg:readonly`、`im:message.group_at_msg:readonly`、`im:message:send_as_bot`、`im:resource`。舊版主控台若仍顯示 `im:message.p2p_msg`、`im:message.group_at_msg`、`im:message`，一併開通。
3. 先不要儲存「使用长连接接收事件」。當時還沒有在線的 client，後台存不下去。
4. 在 Grok Bot 桌面版建立 webhook routine，把 [routine/prompt.md](routine/prompt.md) 貼上並儲存。依 [routine/README.md](routine/README.md) 複製 POST URL、sender key，以及面板上的 header。
5. 在與 Grok Bot 共用檔案系統的 Linux 上安裝：`bash deploy/install.sh`。
6. 編輯 `.env`（由 `.env.example` 複製而來）。填入飛書憑證、webhook URL 與 key。允許名單可以先留空。
7. 若面板 header 不是 `Authorization: Bearer <key>`，改 `GROKBOT_WEBHOOK_AUTH_HEADER` 與 `GROKBOT_WEBHOOK_AUTH_SCHEME`。格式以面板為準，這個儲存庫不另外發明一種。
8. 啟動：`bash deploy/run.sh`。日誌出現 connected 再進行下一步。
9. 回到飛書後台，訂閱方式選 **使用长连接接收事件** 並儲存，添加事件 `im.message.receive_v1`，然後建立版本並發布，可用範圍包含你自己。
10. 私訊機器人。若日誌出現 `rejected sender`，把裡面的 `open_id` 寫進 `FEISHU_ALLOWED_OPEN_IDS`，重啟橋接。
11. 再私訊 `/ping`，應收到 `pong`。接著送一句真正的任務，應先看到「已收到，處理中…」，完成後看到回覆。
12. 群組裡要 @ 機器人才會受理。建議設定 `FEISHU_BOT_OPEN_ID`。
13. 只用一個行程。飛書是叢集模式，多個 client 會把同一則事件隨機送給其中一個。
14. 要常駐時用 [deploy/feishu-grokbot-bridge.service](deploy/feishu-grokbot-bridge.service) 或 [deploy/supervisord.conf](deploy/supervisord.conf)。兩者都會自動重啟。
15. 雲端電腦重建後，先還原 `.env`，再執行 `bash deploy/install.sh && bash deploy/run.sh`。

## 本地指令

這些不會呼叫 Grok Bot：

| 指令 | 作用 |
| --- | --- |
| `/help` | 指令說明 |
| `/ping` | 橋接是否在線 |
| `/status` | 這個聊天進行中的任務 |
| `/history` | 最近 5 筆任務 |

## 環境變數

必填：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`GROKBOT_WEBHOOK_URL`、`GROKBOT_WEBHOOK_KEY`。

其餘預設與說明見 [.env.example](.env.example)。`FEISHU_ALLOWED_OPEN_IDS` 空著等於拒絕所有人。`BRIDGE_OUTBOX_DIR` 預設 `./outbox`。

檢查設定與健康檔、不連外網：

```bash
python -m bridge.main --check
```

## 文件

- [架構](docs/architecture.md)
- [飛書應用設定](docs/feishu-app-setup.md)
- [任務／結果 JSON](docs/protocol.md)
- [安全](docs/security.md)

## 開發

需要 Python 3.11+。

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
```

測試不打飛書，也不打 webhook。CI 在 push 與 pull request 上跑 ruff 與 pytest（3.11、3.12）。

## English

This process lets a Feishu (Lark) user assign work to a Grok Bot assistant and get the reply back in Feishu.

Feishu events arrive only through the official long connection: `lark.ws.Client` in `lark-oapi` 1.7.5, handler `register_p2_im_message_receive_v1`. There is no Feishu HTTP callback. The handler deduplicates, checks the allowlist, enqueues, and returns. A dispatcher then replies with an interactive card and POSTs the task JSON to the routine webhook. The routine writes result files into the outbox directory from the task (`reply_via.dir`). The bridge watches that directory and patches or sends Feishu messages. Feishu app credentials never leave this process.

Setup, short form:

1. Create an enterprise custom app, enable the bot, and copy App ID / App Secret.
2. Grant `im:message.p2p_msg:readonly`, `im:message.group_at_msg:readonly`, `im:message:send_as_bot`, and `im:resource`.
3. Do not save “receive events over the long connection” yet.
4. Create a Grok Bot webhook routine, paste `routine/prompt.md`, and copy the POST URL, sender key, and the header the desktop panel shows.
5. `bash deploy/install.sh`, then fill `.env`. The default auth header is `Authorization: Bearer <key>`. If the panel shows a different header, set `GROKBOT_WEBHOOK_AUTH_HEADER` and `GROKBOT_WEBHOOK_AUTH_SCHEME` to match. An empty scheme sends the key as the raw header value.
6. `bash deploy/run.sh` and wait until the log says connected.
7. In the Feishu console, save long-connection mode, subscribe to `im.message.receive_v1`, and publish.
8. DM `/ping`, then a real task. In groups, @ the bot. Keep a single process running.
9. After the cloud computer is recreated: `bash deploy/install.sh && bash deploy/run.sh`.

`FEISHU_ALLOWED_OPEN_IDS` empty means reject everyone. Run one instance: Feishu cluster mode delivers each event to a random client. See `docs/` for the protocol, security notes, and architecture.
