# 任務與結果協定

English: the bridge POSTs a version-1 task object to the Grok Bot webhook. The routine writes `<task_id>.<seq>.json` files into `reply_via.dir`. Schemas live in `docs/schemas/`.

## 任務（橋接 → Grok Bot）

`POST GROKBOT_WEBHOOK_URL`，`Content-Type: application/json`。

授權標頭不是這個檔案能定死的。預設是：

```http
Authorization: Bearer <GROKBOT_WEBHOOK_KEY>
```

請對照 routine 面板上可複製的 header。不一致時改 `GROKBOT_WEBHOOK_AUTH_HEADER` 與 `GROKBOT_WEBHOOK_AUTH_SCHEME`（scheme 留空表示 header 的值就是 key 本身）。橋接不會再加第二個驗證標頭。

HTTP 2xx 只代表 Grok Bot 收下並開始一次 run，不代表任務做完。其他 4xx（除了 429）不會重試。429、5xx 與連線錯誤會指數退避重試。重試可能在逾時邊界上啟動第二次 run，所以 routine 看到同一個 `task_id` 時應沿用同一個序號、不要開第二份最終回覆。

```json
{
  "version": 1,
  "task_id": "2f1c0e3a-7c1d-4f0a-9b2e-6d5a1c8e0f11",
  "source": "feishu",
  "chat_id": "oc_xxx",
  "chat_type": "p2p",
  "message_id": "om_xxx",
  "root_id": "",
  "sender": {"open_id": "ou_xxx", "name": ""},
  "text": "幫我整理今天的會議",
  "history": [
    {"role": "user", "text": "上一句", "ts": "2026-10-28T00:00:00+00:00"},
    {"role": "assistant", "text": "上一則回覆", "ts": "2026-10-28T00:01:00+00:00"}
  ],
  "reply_via": {"type": "outbox", "dir": "/abs/path/outbox"},
  "created_at": "2026-10-28T00:02:00+00:00"
}
```

| 欄位 | 說明 |
| --- | --- |
| `version` | 固定 `1`。 |
| `task_id` | UUID。結果檔名用它。 |
| `source` | 固定 `feishu`。 |
| `chat_type` | `p2p` 私訊，或 `group` 群組。 |
| `root_id` | 飛書話題根訊息，沒有就是空字串。 |
| `sender.name` | 接收訊息事件沒有顯示名稱，通常是空字串。 |
| `text` | 去掉 @ 佔位符之後的純文字。很長的訊息會截到約 2 萬字。 |
| `history` | 這個聊天最近 N 輪（`BRIDGE_HISTORY_TURNS`，預設 10），不含目前這則。 |
| `reply_via.dir` | outbox 的絕對路徑。routine 必須寫到這個目錄。 |

JSON Schema：`docs/schemas/task.schema.json`。

## 結果（Grok Bot → 橋接）

檔名：`<task_id>.<seq>.json`。`seq` 從 1 起，同一任務遞增。

請先寫到同目錄的暫存檔再 `rename`，避免橋接讀到半份 JSON。若直接寫入最終檔名，無效 JSON 會在 `BRIDGE_OUTBOX_PARTIAL_GRACE_SECONDS`（預設 5 秒）內被留下重試，逾時則移到 `outbox/failed/`。

```json
{
  "version": 1,
  "task_id": "2f1c0e3a-7c1d-4f0a-9b2e-6d5a1c8e0f11",
  "kind": "final",
  "text": "整理好了。",
  "attachments": [{"path": "/abs/path/notes.md"}]
}
```

| `kind` | 橋接做什麼 |
| --- | --- |
| `progress` | 把藍色處理中卡片改成這段文字。長任務可以寫多份。 |
| `final` | 卡片改成綠色「已完成」，並把全文送到聊天。超過約 4000 字會拆成多則；超過約 12000 字會改上傳一個 markdown 檔。 |
| `error` | 卡片改成紅色。 |

`text` 用 markdown。`attachments[].path` 必須是絕對路徑，而且橋接行程讀得到。圖片走 `im.v1.image.create`（`image_type=message`），其他檔案走 `im.v1.file.create`。

處理成功的檔案移到 `outbox/processed/`。格式不對、對不上任務、或 `version` 不是 1，移到 `outbox/failed/`。日誌只記檔名與原因，不記內文。

一個任務寫一份 `final` 或一份 `error` 即可。飛書的 `uuid` 讓同一段回覆在一小時內重送不會變成兩則；行程在送出後、移檔前崩潰時，仍可能再送一次。

JSON Schema：`docs/schemas/result.schema.json`。
