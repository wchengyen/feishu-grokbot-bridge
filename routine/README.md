# 建立 Grok Bot webhook routine

English: create the routine in the Grok Bot desktop app, paste `routine/prompt.md`, save it, then copy the POST URL, sender key, and the header shown on the panel into `.env`. A 200 only means a run started.

桌面版才看得到 webhook URL 與 sender key。手機上看不到這兩個欄位。

1. 打開 Grok Bot，進入這個助理的 Routines。
2. 新增一個 routine，觸發方式選 webhook。
3. 把 `routine/prompt.md` 的正文貼進指示。不要改成工具呼叫清單；照那段意圖即可。
4. 儲存並保持 Active。儲存之後，面板才會出現 POST URL、key，以及一個可以整段複製的 header。
5. 複製三個欄位。URL 放進 `GROKBOT_WEBHOOK_URL`，key 放進 `GROKBOT_WEBHOOK_KEY`。不要把 key 貼回聊天。
6. 對一下面板上的 header 與預設值 `Authorization: Bearer <key>`。Cursor 的 Routines 說明寫面板上的 header 就是這一行，所以橋接預設這樣送。若你面板上的字不一樣，以面板為準，修改：
   - `GROKBOT_WEBHOOK_AUTH_HEADER`：標頭名稱
   - `GROKBOT_WEBHOOK_AUTH_SCHEME`：scheme。留空表示標頭的值就是 key 本身，不加 `Bearer` 這類前綴
7. 重啟橋接。只留一個行程。
8. 從飛書送一則任務。HTTP 200 只代表 run 已開始。回覆會出現在飛書，也會留在 Grok Bot 的 run history。routine 與橋接必須看得到同一個 outbox 目錄；任務 JSON 的 `reply_via.dir` 是絕對路徑。

key 若在面板上換過，要重抄進 `.env` 再重啟。暫停 routine 後，webhook 不會再啟動 run。
