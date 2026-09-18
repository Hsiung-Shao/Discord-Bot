# 表情回應領取身分組 (`commands.reaction_roles`)

管理員在頻道貼一則面板訊息,綁定「表情符號 → 身分組」,成員點該表情就自動獲得身分組,
移除表情就收回。設定按**伺服器**分層存在 `data/reaction_roles.json`,
每個伺服器各自一份對照表,互不干擾。

面板綁在 **訊息 ID** 上而不是按鈕,所以 bot 重啟後不需要做任何事,舊面板自動繼續運作。

---

## 開始前:三個前置條件

沒設好的話指令會成功、但成員點了拿不到身分組,這三項務必先確認:

| 項目 | 怎麼做 |
|---|---|
| **Server Members Intent** | Discord Developer Portal → 你的 App → Bot → 開啟 `SERVER MEMBERS INTENT` |
| **管理身分組權限** | 伺服器設定 → 身分組 → bot 的身分組要有「管理身分組 (Manage Roles)」 |
| **身分組位階** | bot 自己的身分組要**拖到**所有要發放的身分組**上方**,Discord 不允許發放比自己高的身分組 |

位階不對時 `/rr add` 會直接擋下並告訴你該怎麼調,不會等到成員點下去才失敗。

還有一項:要用這功能的伺服器,其 guild ID 要寫進 `.env` 的 `GUILD_IDS`,否則 `/rr` 指令不會出現在該伺服器。

---

## 快速開始

```
/rr create channel:#領取身分組 title:🎭 領取身分組 description:點下方表情就能拿到對應身分組
   ← 回覆會給你這則面板的訊息 ID

/rr add message_id:<剛拿到的ID> emoji:🔔 role:@開服通知
/rr add message_id:<剛拿到的ID> emoji:🎮 role:@遊戲揪團
```

`/rr add` 會自動幫面板加上該表情,並把對照表寫進面板 embed。成員直接點就能領。

`message_id` 有**自動補完**,打幾個字就會跳出本伺服器已建的面板,不用真的去複製 ID;
也可以直接把**訊息連結**整條貼進去。

---

## 指令一覽

所有指令都需要**管理伺服器**權限,回覆一律只有自己看得到 (ephemeral)。

| 指令 | 說明 |
|---|---|
| `/rr create channel: [title:] [description:] [color:]` | 在指定頻道發一則面板訊息並建檔,回傳訊息 ID |
| `/rr add message_id: emoji: role:` | 綁定表情 → 身分組;bot 自動加上該表情並更新面板 |
| `/rr remove message_id: emoji:` | 解除綁定,清掉面板上的該表情 |
| `/rr list` | 列出本伺服器所有面板、所在頻道與對照表 |
| `/rr delete message_id: [delete_message:]` | 移除面板設定;`delete_message:True` 連訊息一起刪 |
| `/rr bind channel: message_id:` | 把一則**既有訊息**納管成面板 |
| `/rr sync message_id:` | 掃描面板現有的表情回應,補發 bot 離線期間漏掉的身分組 |

`description` 想換行就打 `\n`。`color` 吃 `#5865F2` / `0x5865F2` / `5865F2` 三種寫法。

---

## 幾個要知道的行為

**解綁不會收回已領的身分組**。`/rr remove` 和 `/rr delete` 只是讓「之後點了不再給」,
已經在成員身上的身分組要另外手動處理 — 這是刻意的,避免一次誤操作把全伺服器的身分組掃掉。

**bot 離線期間的點擊不會補**。Discord 不會把離線期間的表情事件補送給 bot。
開機後跑 `/rr sync <面板ID>` 會掃過該訊息現有的所有表情回應,把漏掉的補發。
`sync` **只補發、不收回**(不會因為某人沒點表情就拔掉他手上的身分組)。

**面板訊息被刪掉時,設定會自動清掉**,不會留下孤兒資料。

**可以多領**:一個面板上的身分組互不影響,成員想領幾個就領幾個(沒有互斥/單選模式)。

**自訂 emoji** 必須來自 bot 也在的伺服器,否則 Discord 不讓 bot 加上去,`/rr add` 會直接報錯。
綁定後就算之後把 emoji 改名也不受影響 — 設定裡記的是 emoji **ID** 不是名字。

**`/rr bind` 納管別人發的訊息**時,bot 編輯不了那則訊息,所以對照表不會自動寫進去,
表情本身照常有效,但要顯示「哪個表情給哪個身分組」得你自己寫在訊息內容裡。

**單一面板最多 20 個表情**(Discord 對單則訊息的限制),超過請另外開一個面板。

---

## 設定檔格式 (`data/reaction_roles.json`)

首次啟動自動建立,正常情況下不需要手動編輯。手動改完要重啟 bot 才會生效。

```json
{
    "guilds": {
        "伺服器ID": {
            "panels": {
                "面板訊息ID": {
                    "channel_id": 123456789,
                    "title": "🎭 領取身分組",
                    "description": "點下方表情即可領取",
                    "color": 5793266,
                    "editable": true,
                    "mappings": [
                        { "emoji_key": "🔔", "emoji_raw": "🔔", "role_id": 111 },
                        { "emoji_key": "998877", "emoji_raw": "<:poe:998877>", "role_id": 222 }
                    ]
                }
            }
        }
    }
}
```

- `emoji_key` 是比對用的鍵:自訂 emoji 存 **ID**,Unicode emoji 存字元本身
- `emoji_raw` 只用於顯示與重新加表情
- `editable` = 這則訊息是不是 bot 自己發的(決定面板內容能不能自動更新)

---

## 出問題時

日誌寫在 `logs/bot.log`,只記 WARNING 以上(權限不足、身分組被刪、設定檔壞掉);領取成功不留紀錄。

| 症狀 | 多半是 |
|---|---|
| 點了沒反應,log 有 `無權限` | bot 的身分組位階低於目標身分組,或缺「管理身分組」權限 |
| 點了沒反應,log 什麼都沒有 | 該訊息不是登記過的面板(用 `/rr list` 確認 ID),或 Members Intent 沒開 |
| `/rr` 指令在某伺服器看不到 | 該伺服器的 ID 不在 `.env` 的 `GUILD_IDS`,加上後重啟 bot |
| log 出現 `綁定的身分組 … 已不存在` | 身分組被刪了,用 `/rr remove` 清掉該表情的綁定 |
