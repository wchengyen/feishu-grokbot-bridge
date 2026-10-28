# 飛書應用設定

這份文件說明如何建立**企業自建應用**，讓橋接用長連接收到訊息。介面上的按鈕名稱沿用飛書開發者後台的簡體字，方便對照。

English: create an enterprise custom app, enable the bot, grant the scopes below, start this bridge, then save **使用长连接接收事件** and subscribe to `im.message.receive_v1`. Long connection can be saved only while a client is online, and only custom apps support it.

## 1. 建立應用

1. 打開 [飛書開發者後台](https://open.feishu.cn/app)。國際版 Lark 用 [open.larksuite.com](https://open.larksuite.com/app)，並把 `FEISHU_DOMAIN` 設成 `https://open.larksuite.com`。
2. 建立**企业自建应用**。商店應用不能用長連接。
3. 在「憑證與基礎信息」複製 App ID、App Secret，寫進橋接機器的 `.env`。不要貼到聊天或 git。

## 2. 開啟機器人

「應用能力」→ 添加「机器人」。沒有機器人能力就收不到、也發不出訊息。

可用範圍要包含會私訊或在群裡 @ 機器人的人。測試階段可以用後台的測試企業，權限變更會直接生效；正式環境要發版。

## 3. 權限

在「權限管理」開通**應用身份**權限。接收私訊與接收群組 @ 要分開申請。

| 用途 | 建議權限 | 舊版名稱（主控台若仍顯示） |
| --- | --- | --- |
| 私訊 | `im:message.p2p_msg:readonly` 讀取用戶發給機器人的單聊消息 | `im:message.p2p_msg` |
| 群組裡 @ 機器人 | `im:message.group_at_msg:readonly` 接收群聊中 @ 機器人消息事件 | `im:message.group_at_msg` |
| 以機器人發訊息、回覆、更新卡片 | `im:message:send_as_bot` | `im:message`（獲取與發送單聊、群組消息，部分租戶的回覆／更新介面仍列出它） |
| 上傳圖片與檔案 | `im:resource` |  |

不要為了這個橋接去開「獲取群組中所有消息」（`im:message.group_msg`）。那個範圍太寬。若真的開了，請同時設定 `FEISHU_BOT_OPEN_ID`，否則任何具體 @ 都會被當成在叫機器人。

群裡只要用戶 @ 機器人、不要其他機器人的訊息時，用上面的 `im:message.group_at_msg:readonly` 即可。

## 4. 事件訂閱：長連接

事件是 `im.message.receive_v1`（接收消息 v2.0）。橋接註冊的是 lark-oapi 1.7.5 的 `EventDispatcherHandler.register_p2_im_message_receive_v1`。`p2` 表示 v2.0。

長連接沒有對外的 HTTP callback，也不需要公網 IP 或加密 key。SDK 在握手時用 App ID / App Secret 換取 WebSocket 位址。事件處理函式走 `EventDispatcherHandler._do_without_validation`，所以程式裡的 encrypt key 與 verification token 留空，跟官方範例一樣。

限制：

- 只支援企業自建應用。
- 處理函式要在約 3 秒內返回，而且不能拋出例外，否則飛書會重推。本橋接在回呼裡只做去重、允許名單、入列。
- 叢集模式：同一個應用如果有多個 client，每則事件只會隨機送到其中一個。請只跑一個行程。程式會對資料庫鎖檔做 `flock`，第二個行程會直接退出。
- 後台要能存「使用长连接接收事件」，當下必須已經有 client 在線。先啟動橋接，看到日誌 `connected`，再回到後台儲存。

步驟：

1. 填好 `.env` 並執行 `bash deploy/run.sh`，確認日誌裡有 connected。
2. 「事件與回調」→「事件配置」→ 訂閱方式選 **使用长连接接收事件** → 儲存。
3. 添加事件 **接收消息** `im.message.receive_v1`。
4. 「版本管理與發布」建立版本並發布。測試企業可以先裝測試版。

發版完成後，私訊機器人。若允許名單是空的，日誌會出現 `rejected sender` 與 `sender_open_id`。把那個 open_id 寫進 `FEISHU_ALLOWED_OPEN_IDS` 再重啟。開發者後台的事件日誌裡也能看到 `event.sender.sender_id.open_id`。

## 5. 機器人收得到、但回不出

- 確認 `im:message:send_as_bot` 已開通且版本已發布。
- 確認使用者在應用可用範圍內。
- 卡片更新用的是 `im.v1.message.patch`，只能改機器人自己發出的互動卡片。確認第一則回覆是「已收到，處理中…」。
- 上傳失敗時確認 `im:resource`，以及附件小於 30MB。
