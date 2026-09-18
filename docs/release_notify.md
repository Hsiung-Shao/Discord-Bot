# 專案版本更新公告 (`commands.release_notify`)

多專案訂閱制的版本更新公告推送。每個專案各自設定「更新來源 + 推送頻道 + 通知身分組」,
全部用 `/release` slash 指令管理,改完即時生效,不需要重啟 bot。

---

## 三種更新來源

| 來源 | 說明 | 適用情境 |
|---|---|---|
| `github` | 定期輪詢 GitHub Releases API,有新 release 自動推送 | 有在 GitHub 發 release 的專案 |
| `file` | 讀一個本地 JSON 檔,`version` 變了就推送 | 沒用 GitHub release,或想由自己的建置腳本 / CI 產生公告內容 |
| `manual` | 完全不自動抓,只靠 `/release publish` 手動發 | 臨時公告、不在 GitHub 上的專案 |

`file` 來源的路徑是**選填**的 — 沒填就等於停用這個來源,填了才會去讀。
已設定過的路徑之後想拿掉,用 `/release edit key:<代號> file_path:-`(填一個減號即清除)。

---

## 快速開始

```
/release add key:mybot display_name:我的機器人 source:GitHub Releases repo:hsiungshao/DiscordBot channel:#更新公告 role:@訂閱更新
/release test key:mybot          ← 先確認頻道發得出去、身分組真的會跳通知
```

新增專案時會**先把來源當下的既有版本全部記為「已推送」但不發送**,避免一次把歷史版本灌爆頻道。
之後有新版本才會自動推。想立刻看看真實版本長什麼樣:

```
/release check key:mybot resend_latest:True
```

---

## 指令一覽

所有指令都需要**管理伺服器**權限,回覆一律只有自己看得到 (ephemeral)。

| 指令 | 說明 |
|---|---|
| `/release add` | 新增專案。必填 `key`(代號)、`display_name`、`source`、頻道;`repo` / `file_path` 依來源而定 |
| `/release remove key:` | 移除專案 |
| `/release list` | 列出所有專案:來源、推送目標、最新已推送版本、最後檢查時間 |
| `/release target_add key: channel: role:` | 追加推送頻道,或為既有頻道追加通知身分組 |
| `/release target_remove key: channel: [role:]` | 移除頻道;只填 `role` 時保留頻道但取消 tag 該身分組 |
| `/release edit key: ...` | 改顯示名稱、來源、repo、檔案路徑、pre-release 開關、顏色、啟用停用 |
| `/release check [key:] [resend_latest:]` | 立即檢查(不填 `key` 檢查全部);`resend_latest:True` 忽略去重把最新一筆重推 |
| `/release publish key:` | 彈出視窗手動填「版本號 / 標題 / 內容 / 連結」立即發布 |
| `/release test key: [channel:]` | 發測試公告,驗證權限與身分組通知 |
| `/release interval minutes:` | 調整自動檢查間隔,即時生效不必重啟 |

`key` 參數有自動補完,打幾個字就會跳出已設定的專案。

**跨伺服器頻道**:`channel` / `role` 的下拉選單只列得出當前伺服器的項目。要推到別的伺服器,
改用 `channel_id` / `role_id` 參數直接填 ID(填了會蓋過下拉選單的選擇)。

---

## 本地 JSON 檔案格式 (`source:file`)

單一版本:

```json
{
  "version": "1.2.0",
  "title": "新增自動翻譯功能",
  "date": "2026-07-26",
  "url": "https://github.com/owner/repo/releases/tag/v1.2.0",
  "highlights": ["支援 NLLB 離線翻譯", "推送速度快兩倍"],
  "body": "## 新增\n- 自動翻譯頻道\n\n## 修正\n- 修掉排程重複觸發"
}
```

- **`version` 是必填**,同時作為去重依據 — 值沒變就不會重複推送。
- 其餘全部選填:`title` 省略時用 `version` 當標題;`highlights` 會以 `•` 條列排在 `body` 前面。
- `body` 支援 Discord Markdown:`## 標題`、`- 條列`、`**粗體**`、`` ```程式碼``` ``。

要一次放多筆(例如補歷史版本),用陣列包起來,會依 `date` 由舊到新逐則推送:

```json
{ "releases": [ { "version": "1.1.0", "body": "..." }, { "version": "1.2.0", "body": "..." } ] }
```

---

## GitHub Token(選填但建議)

在 `.env` 設定 `GITHUB_TOKEN=`:

- **private repo 必須設定**,否則會回「找不到 repo」。
- public repo 不設也能用,但 API 額度只有 **60 次/小時/IP**;設了變成 5000 次/小時。
- 到 GitHub → Settings → Developer settings → Personal access tokens 申請。
  Fine-grained token 對 public repo 不需要任何 scope;private repo 需給該 repo 的 **Contents: Read-only**。

程式已使用 ETag 條件式請求,內容沒變時 GitHub 回 304 **不計入額度**,一般用量不會爆。

---

## 常見問題

**Q: 公告發出來了,但身分組沒有跳通知?**
Discord 的身分組預設不允許被任意成員提及。到「伺服器設定 → 身分組 → 該身分組」把
**「允許任何人 @ 提及此身分組」** 打開;或給 bot 的身分組「提及 @everyone、@here 和所有身分組」權限。
`/release test` 就是用來當場確認這件事的 — 訊息上方的 `@身分組` 有黃色高亮才代表通知成功送出。

**Q: 一次發了太多則怎麼辦?**
單一專案每輪最多推 5 則,其餘下一輪續推。新增專案時的 seed 機制也會擋掉歷史版本回填。

**Q: 更新內容太長會被切掉嗎?**
Discord Embed 上限 4096 字,超過的部分會在換行處截斷並附上「查看完整更新內容」連結。

**Q: 設定存在哪?**
`data/release_notify.json`(路徑可用 `.env` 的 `RELEASE_NOTIFY_FILE` 改)。
`data/` 已在 `.gitignore` 內,不會進版控。日誌在 `logs/bot.log`(只記 WARNING 以上)。自動檢查間隔預設 300 分鐘(5 小時)。

**Q: 指令沒出現在 Discord?**
slash 指令靠 `bot.py` 啟動時同步到 `.env` 的 `GUILD_IDS` 所列伺服器,重啟 bot 即可。
新伺服器記得把 ID 加進 `GUILD_IDS`。
