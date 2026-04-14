# OpenCode Tool Guide

以 OpenCode 原始碼為基礎，整理 coding agent 所有 tool 的實作原理。
原始碼位置：`packages/opencode/src/tool/`

---

## 架構概覽

每個 tool 由兩個檔案組成：

- **`.ts`** — 工具實作，定義參數 schema（Zod）與 `execute` 邏輯
- **`.txt`** — 工具描述，純文字，在 runtime 用 `import` 載入成字串，直接傳給 LLM

組裝成 function call 的鏈路：

```
.txt → description 字串
Zod schema → z.toJSONSchema() → JSON Schema
                ↓
          tool({ description, inputSchema, execute })   ← Vercel AI SDK
                ↓
          streamText({ tools, messages, model })
                ↓
          LLM provider 原生格式（Anthropic tool_use / OpenAI function_call）
```

---

## Must-have（缺了 agent 就廢）

### read

**原理：** Node.js `readline` + `createReadStream` 逐行讀，非 `fs.readFile` 一次讀完。

核心行為：
- 路徑是目錄 → 列 entries（每個目錄加 `/` 後綴）
- 路徑是圖片或 PDF → 讀取 binary → base64 → 當 attachment 回傳給 LLM
- 二進位偵測：先查副檔名白名單（`.zip/.exe/.pyc`...），再掃前 4096 bytes，非可印字元 > 30% 就拒絕
- **分頁**：`offset`（1-indexed 行號）+ `limit`（預設 2000 行），上限 50KB
- 輸出格式：`行號: 內容`，讓 LLM 在 edit 時能精確指定位置
- 讀到 `.env` 類敏感檔案前會觸發 permission 請求

找不到檔案時，會掃同目錄找相似名字，給 LLM「你是不是要找這個？」的提示。
演算法是**雙向 substring 比對**（不是 Levenshtein，沒有編輯距離）：

```
條件 A：目錄裡的檔名.toLowerCase() 包含 你給的名字.toLowerCase()
條件 B：你給的名字.toLowerCase() 包含 目錄裡的檔名.toLowerCase()
滿足其中一個就列入候選，最多回傳 3 筆
```

範例：

| LLM 找的 | 目錄裡有 | 結果 |
|---------|---------|------|
| `userservice.ts` | `UserService.ts` | ✅ lowercase 後互相包含 |
| `user` | `user-service.ts` | ✅ `user-service.ts` 包含 `user` |
| `userServiceHandler.ts` | `userService.ts` | ✅ `userservicehandler` 包含 `userservice` |
| `userService.ts` | `user-service.ts` | ❌ `-` 讓 substring 不匹配，找不到 |

讀不到目錄本身（權限問題等）就靜默回傳空陣列。

---

### write

**原理：** 整個覆蓋，寫入前先生成 diff 給使用者看。

核心行為：
1. 讀舊內容 → 用 `diff` 套件的 `createTwoFilesPatch` 生成 unified diff
2. 把 diff 帶入 `ctx.ask(permission: "edit")` → 使用者確認後才寫入
3. `fs.writeFile(filepath, content)` 覆蓋
4. 呼叫 formatter（prettier 等）
5. 通知 LSP server 更新 → 收集 type error → 附在 output 讓 LLM 自己修

適合用來**新建檔案**，或需要大幅重寫整個檔案的情況。

---

### edit

**原理：** `oldString` → `newString` 的精確字串替換，有 9 層 fallback 應對 LLM 給出不精確的 oldString。

LLM 給的 `oldString` 常常跟檔案不完全一樣（縮排差一格、多個空白等），所以替換策略按順序嘗試：

| 層 | Replacer | 策略 |
|----|----------|------|
| 1 | `SimpleReplacer` | `indexOf` 完全比對（最嚴格） |
| 2 | `LineTrimmedReplacer` | 每行 `.trim()` 後再比對 |
| 3 | `BlockAnchorReplacer` | 用第一行和最後一行當錨點，中間用 **Levenshtein 相似度**判斷 |
| 4 | `WhitespaceNormalizedReplacer` | 所有空白壓成單個空格後比對 |
| 5 | `IndentationFlexibleReplacer` | 去掉最小共同縮排後比對 |
| 6 | `EscapeNormalizedReplacer` | 把 `\n` `\t` 等反跳脫後比對 |
| 7 | `TrimmedBoundaryReplacer` | 去掉頭尾空白後比對 |
| 8 | `ContextAwareReplacer` | 用頭尾行當錨，中間行 ≥50% 相似就接受 |
| 9 | `MultiOccurrenceReplacer` | 允許多個 match（配合 `replaceAll` 參數） |

找到**唯一 match** 才替換；找到多個 match 就報錯要求 LLM 提供更多上下文。

寫入後同樣觸發 formatter 和 LSP 診斷。

---

### bash

**原理：** 執行前先用 **tree-sitter** 解析 AST，找出涉及的路徑做 permission check，再 spawn child process 執行。

執行前掃描流程：
1. tree-sitter 把 shell command parse 成 AST
2. 找出所有 `rm`、`cp`、`mv`、`mkdir` 等操作，解析出目標路徑
3. 路徑在工作目錄外 → `ctx.ask(permission: "external_directory")`
4. 一般 command → `ctx.ask(permission: "bash")`，pattern 是指令本身

執行：
- `ChildProcess.spawn()` 以 Effect Stream 形式即時串流 stdout/stderr
- 支援 `workdir` 參數（不用 `cd &&`）
- `timeout`（預設 2 分鐘）和 `AbortSignal` 控制
- timeout 到期 → `SIGKILL`，等 3 秒強制殺

description 裡的 `${os}`、`${shell}`、`${chaining}` 在 runtime 動態替換，讓 LLM 知道現在在什麼環境。

---

### glob

**原理：** 底層跑 **ripgrep** 的 `--files` 模式，不是 JS 原生 `glob`。

```sh
rg --files --glob <pattern> <directory>
```

- 結果取 stat 拿 `mtime`，按**修改時間降冪**排序（最近改的排前面）
- 最多回傳 100 筆，截斷時告知 LLM
- 優先用 ripgrep 的好處：速度快、自動 respect `.gitignore`

---

### grep

**原理：** 直接 spawn **ripgrep** process，解析輸出。

```sh
rg -nH --hidden --no-messages --field-match-separator=| --regexp <pattern> <path>
```

- `-nH` = 顯示行號 + 檔名
- `--field-match-separator=|` 用 `|` 分隔欄位，方便 parse
- 輸出格式：`filepath|linenum|content`，parse 後重組為可讀格式
- 同樣按 `mtime` 排序，最多 100 筆
- exit code 0 = 有 match，1 = 無 match，2 = 有錯誤但可能有部分結果

---

## Good-to-have（加了能力明顯提升）

### webfetch

**原理：** HTTP GET + HTML → Markdown 轉換。

```
GET <url>
  → 讀 Content-Type
  → 圖片 → base64 attachment
  → HTML → Turndown 轉 Markdown
  → 其他 → 原始文字
```

細節：
- 偽裝成瀏覽器 User-Agent 避免被擋
- 遇到 Cloudflare 403（`cf-mitigated: challenge`）→ 改用真實 UA 重試
- 限制 5MB，timeout 預設 30 秒（最大 120 秒）
- 支援三種回傳格式：`markdown`（預設）、`text`（HTML 去標籤）、`html`（原始）

---

### websearch

**原理：** 呼叫 **Exa MCP** endpoint，用 JSON-RPC over SSE 協議。

```
POST https://mcp.exa.ai/mcp
Body: {
  jsonrpc: "2.0",
  method: "tools/call",
  params: { name: "web_search_exa", arguments: { query, type, numResults, livecrawl } }
}
Response: SSE stream → 解析 "data: " 行 → 取 result.content[0].text
```

參數：
- `type`: `auto`（預設）/ `fast` / `deep`
- `livecrawl`: `fallback`（快取優先）/ `preferred`（即時爬取優先）
- `numResults`: 預設 8
- `contextMaxCharacters`: 預設 10000

與 `webfetch` 的分工：websearch 找**哪些頁面相關**，webfetch 讀**某個頁面的完整內容**。兩個都給 LLM 才能做完整的網路研究。

---

### question

**原理：** 暫停 agent loop，向使用者發問，拿到答案後繼續。

```
execute() → question.ask({ questions }) → 等使用者回答 → 把答案格式化回傳給 LLM
```

問題可以是：
- 單選 / 多選選項（`options` 陣列）
- 自由輸入（`custom: true`）

回傳格式：`"問題1"="答案1", "問題2"="答案2"`

用途：遇到歧義時問清楚，不要瞎猜。是 permission system 的「對話」延伸。

---

### multiedit

**原理：** `edit` tool 的批次包裝，同一個檔案多處修改一次完成。

```ts
for (const entry of params.edits) {
  result = await edit.execute({ filePath, oldString, newString, replaceAll })
}
return results.at(-1).output
```

每個 edit 按順序執行（前一個的結果是下一個的輸入），全部成功才算完成。
每次 edit 都會觸發一次 permission ask 和 LSP 診斷。

用途：重構時一個 function 有多個地方要改，不用來回 read → edit → read → edit。

---

## Nice-to-have（特定需求才用）

### todowrite

**原理：** 在 session 內建立結構化任務清單，純粹是 LLM 的**工作記憶外部化**，不執行任何 IO。

狀態：`pending` / `in_progress`（同時只能一個）/ `completed` / `cancelled`

用途：任務步驟超過 3 個、需要跨多輪對話追蹤進度時使用。對使用者也是進度可視化的手段。

---

### task（sub-agent）

**原理：** 一個 tool，其 `execute` 內部建立新的 child session 並跑完整的 agent loop。

```
primary agent 呼叫 task tool
  → 建立 child session（parentID 指向 parent session）
  → 以指定的 subagent_type 和 prompt 跑 agent loop
  → child loop 結束 → 把最後一條 text message 回傳給 primary agent
```

支援 `task_id` 參數續接同一個 child session（不重開）。
Child session 的工具權限繼承自 agent 設定，可以限制不能再呼叫 task（防止無限遞迴）。

從 LLM 視角來看，呼叫 `task` 跟呼叫 `bash` 結構完全一樣，差別只在 execute 的實作。

---

### lsp

**原理：** 透過 Language Server Protocol 取得靜態分析資訊，而不是靠 grep 猜。

支援 9 個操作：

| 操作 | 功能 |
|------|------|
| `goToDefinition` | 找符號定義位置 |
| `findReferences` | 找所有引用 |
| `hover` | 取得型別資訊 / 文件 |
| `documentSymbol` | 列出檔案內所有符號 |
| `workspaceSymbol` | 跨檔案搜尋符號 |
| `goToImplementation` | 找 interface 的實作 |
| `prepareCallHierarchy` | 建立呼叫階層 |
| `incomingCalls` | 誰呼叫了這個函式 |
| `outgoingCalls` | 這個函式呼叫了誰 |

需要有對應語言的 LSP server 在跑（ts-language-server、pyright 等），否則直接報錯。

---

### codesearch

**原理：** 呼叫 Exa 的 `get_code_context_exa` MCP tool，語意搜尋程式碼範例和 API 文件。

與 `websearch` 同樣走 `https://mcp.exa.ai/mcp` JSON-RPC，但用不同的 tool name。

差別：
- `websearch` → 通用網頁搜尋，回傳網頁摘要
- `codesearch` → 專門找程式碼範例和 SDK 文件，回傳 token 計量的程式碼片段

token 數可調（1000–50000），預設 5000。需要 Exa API 授權，僅在 opencode provider 或設定 `OPENCODE_ENABLE_EXA` 時啟用。

---

### apply_patch

**原理：** 解析 OpenAI 風格的 patch 格式，批次操作多個檔案。

patch 格式（非標準 unified diff，是 OpenAI 自訂格式）：

```
*** Begin Patch
*** Add File: hello.txt
+Hello world
*** Update File: src/app.py
*** Move to: src/main.py
@@ def greet():
-print("Hi")
+print("Hello, world!")
*** Delete File: obsolete.txt
*** End Patch
```

支援操作：`add`（新建）、`update`（修改）、`delete`（刪除）、`move`（改名）

與 `edit` 的差別：
- `edit` 是字串 find-replace，`apply_patch` 是 diff 格式
- GPT 系列模型（gpt-4o 之外）傾向輸出 patch 格式，所以這個 tool 專門給它們用

---

### plan_exit

**原理：** Plan mode 的出口 tool，完成規劃後詢問使用者是否切換到 build agent。

用途非常特定：Plan mode 的 agent 只能讀檔、不能寫檔。寫完計畫後呼叫 `plan_exit`，詢問使用者確認，確認後自動建立一條 synthetic user message 切換到 build agent。這個 tool 正常使用不需要實作。
