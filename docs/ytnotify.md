# YouTube 影片更新通知 (`commands.ytnotify`)

追蹤**單一** YouTube 頻道，有新影片發布時自動推播到指定 Discord 頻道並 tag 身分組。
用 YouTube 官方 RSS feed 輪詢，**不需要申請 API Key，也沒有配額限制**。

---

## `.env` 設定

| 變數 | 說明 |
|---|---|
| `YT_CHANNEL_ID` | 要追蹤的頻道 ID(**不是** `@handle`)。到頻道頁點「共用頻道」複製,或看網址 `/channel/UCxxxxxxxx` 那一段。 |
| `YT_NEWS_THREAD_ID` | 推播目標的 Discord 頻道或討論串 ID。 |
| `YT_NOTIFY_ROLE_ID` | 新影片發布時要 tag 的身分組 ID。留空則只發 embed、不 tag 任何人。 |
| `YT_DATA_FILE` | 去重紀錄檔路徑,預設 `data/ytnotify.json`,首次啟動自動建立。 |
| `YT_CHECK_INTERVAL_MINUTES` | 檢查間隔(分鐘),預設 20。RSS 無配額限制,想更即時可調短。 |

要 tag 身分組通知才會實際跳出提醒,記得去伺服器設定 → 身分組 → 該身分組開啟「允許任何人 @ 提及」,否則只會顯示文字不會通知。

---

## 行為說明

- **只通知一般影片,自動過濾 Shorts**:YouTube 的頻道 RSS 會把 Shorts 跟一般影片混在一起回傳,兩者只能靠 `<link>` 的網址格式分辨(一般影片是 `/watch?v=`,Shorts 是 `/shorts/`)。程式已依此過濾掉 Shorts,不會為它們發通知。
- **首次啟動會 seed**:第一次跑會把目前 RSS 回傳的所有影片(YouTube 通常給最新約 15 部,過濾 Shorts 後可能更少)記錄成「已推送」但**不會發送任何通知**,避免把舊影片全部炸出來。之後才會依新片正常推播。
- **判斷新片用 `videoId`**,不是用發布時間 —— 影片改標題或描述時 RSS 的 `updated` 時間會變,但不會被誤判成新片重推一次。
- **每次最多推 5 則**新影片,若某次同時多出更多,剩下的會留到下一輪繼續推(不會漏)。
- **抓取失敗或頻道 ID 打錯**(RSS 回空)時,該輪會直接跳過並記 log,**不會**清空既有的去重紀錄。
- 排程用 APScheduler,`cog_unload` 時會正常關閉,重啟 bot 不會累積重複的排程。

---

## 手動觸發

```
!ytcheck
```
限 bot 擁有者(`is_owner`)使用,立即檢查一次並回報推送了幾則新影片。

---

## 出問題時

日誌獨立寫在 `logs/ytnotify.log`。

| 症狀 | 多半是 |
|---|---|
| 完全沒推播,log 顯示「YT_CHANNEL_ID 未設定」 | `.env` 沒填 `YT_CHANNEL_ID` |
| 完全沒推播,log 顯示「RSS 解析結果為空」 | `YT_CHANNEL_ID` 打錯,或該頻道近期沒有公開影片 |
| 有推播但沒跳通知(只顯示文字) | 該身分組沒開「允許任何人 @ 提及」,或 `YT_NOTIFY_ROLE_ID` 留空 |
| log 顯示「無法取得推播頻道」 | `YT_NEWS_THREAD_ID` 錯誤,或 bot 沒被加進該頻道/討論串 |
| 剛設定好就想看效果 | 正常情況下第一次是 seed、不推送;可以手動刪掉 `data/ytnotify.json` 最後一筆 ID 再跑 `!ytcheck` 測試 |
