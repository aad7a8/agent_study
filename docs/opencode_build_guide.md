# OpenCode Build Mode Guide

以 OpenCode 原始碼為基礎，說明 build mode（agent loop）的完整運作機制。
相關原始碼：`packages/opencode/src/session/prompt.ts`、`src/session/processor.ts`、`src/session/llm.ts`

---

## 什麼是 Build Mode

Build mode 就是 OpenCode 的**主 agent loop**，是一切執行的核心。
它對應 `build` 這個 named agent，允許寫檔、執行指令、呼叫所有 tool。

和 plan mode 不同，build mode **沒有特殊限制**，就是一個裸跑的 ReAct loop。

---

## 進入點：`prompt()`

```
session/prompt.ts:1268
```

```
使用者送出訊息
      ↓
prompt(input: PromptInput)
  → createUserMessage(input)    ← 把使用者訊息存到 DB
  → sessions.touch(sessionID)   ← 更新 session 時間戳
  → 處理 per-tool permission 覆蓋（input.tools 裡的 allow/deny）
  → if noReply → 直接 return（只存訊息，不回覆）
  → loop({ sessionID }) → runLoop(sessionID)
```

`prompt()` 是唯一的入口。每次使用者輸入都走這裡。

---

## 核心：`runLoop()` while(true)

```
session/prompt.ts:1297
```

這是 ReAct 的本體。每一次迴圈 = 一次 LLM 呼叫 + tool 執行。

### 完整流程（每輪迭代）

```
1. status.set("busy")                         ← 設 UI 狀態為「忙碌中」

2. MessageV2.filterCompactedEffect(sessionID) ← 從 DB 載入所有訊息
                                                 已被 compact 的訊息會被過濾掉

3. 從尾端往前掃描訊息，找出：
   - lastUser（最後一條 user 訊息）
   - lastAssistant（最後一條 assistant 訊息）
   - lastFinished（最後一條有 finish 欄位的 assistant 訊息）
   - tasks（未處理的 subtask / compaction 部件）

4. 判斷退出條件：
   if (lastAssistant.finish && finish != "tool-calls" && !hasToolCalls && lastUser.id < lastAssistant.id)
     break  ← LLM 說 stop，且沒有待處理的 tool call → 結束 loop

5. step++
   if (step === 1) fork title()   ← 第一輪時非同步生成 session 標題

6. 處理 subtask（若有 subtask part）：
   handleSubtask() → continue    ← 讓子 agent 跑完後繼續

7. 處理 compaction（若有 compaction part）：
   compaction.process() → continue / break

8. 自動壓縮檢查：
   if (lastFinished 的 token 數 > 模型上限 * 0.9) → compaction.create() → continue

9. agents.get(lastUser.agent)     ← 取得當前 agent 定義（build / plan 等）

10. insertReminders({ messages, agent, session })
    ← 在 lastUser message 後面注入 synthetic text
    ← plan agent → 注入 READ-ONLY 提示
    ← 從 plan 切回 build → 注入 BUILD_SWITCH 提示（「你現在可以改檔案了」）

11. 建立空的 assistant message 並存到 DB
    { role: "assistant", agent, modelID, providerID, ... }

12. processor.create({ assistantMessage, sessionID, model })
    ← 建立 processor handle（管理 streaming 狀態）

13. resolveTools({ agent, session, model, processor, messages })
    ← 根據 agent permission + model 能力 + MCP，組出這輪可用的 tool 清單

14. [skills, env, instructions, modelMsgs] = 並行取得：
    - SystemPrompt.skills(agent)   ← agent 的技能提示
    - SystemPrompt.environment(model) ← 環境資訊（OS / cwd 等）
    - instruction.system()         ← 使用者自訂的 system instruction
    - MessageV2.toModelMessagesEffect(msgs, model) ← 把 DB 訊息轉成 LLM 格式

15. system = [...env, ...skills, ...instructions]

16. handle.process({ user, agent, system, messages, tools, model })
    ← 主要的 LLM 呼叫，見下節

17. 根據 result 決定下一步：
    - "stop"    → break（退出 loop）
    - "compact" → compaction.create() → continue
    - "continue" → 繼續下一輪迭代
```

---

## LLM 呼叫：`handle.process()`

```
session/processor.ts:533
```

```
process(streamInput: LLM.StreamInput):
  1. llm.stream(streamInput)
     ← 呼叫 Vercel AI SDK 的 streamText()
     ← 回傳 Effect Stream<LLM.Event>

  2. stream.pipe(
       Stream.tap(handleEvent),       ← 每個事件都即時處理
       Stream.takeUntil(needsCompaction), ← context overflow → 提早結束
       Stream.runDrain                ← 把 stream 跑完
     )

  3. 套用 retry 策略（SessionRetry.policy）
     ← 處理 provider 暫時性錯誤（rate limit、timeout 等）

  4. cleanup()
     ← 結束後：把尚未完成的 tool call 標為 error / interrupted

  5. 回傳：
     - "compact" → 需要壓縮（context overflow）
     - "stop"    → tool 被使用者拒絕，或 assistant message 有 error
     - "continue" → 正常結束，繼續 loop
```

---

## Stream 事件處理：`handleEvent()`

```
session/processor.ts:214
```

每個從 LLM 來的 token / tool call / 狀態事件都在這裡處理：

| 事件 | 處理 |
|------|------|
| `start` | 設 status = busy |
| `reasoning-start` | 建立 reasoning part（thinking token）存到 DB |
| `reasoning-delta` | 即時 append 到 reasoning part（streaming） |
| `reasoning-end` | 完成 reasoning part，記錄結束時間 |
| `text-start` | 建立 text part 存到 DB |
| `text-delta` | 即時 append 文字（`updatePartDelta`，delta streaming） |
| `text-end` | 完成 text part，觸發 `experimental.text.complete` plugin hook |
| `tool-input-start` | 建立 pending 狀態的 tool part |
| `tool-input-delta` | （目前忽略，streaming 輸入） |
| `tool-call` | 標記 tool 為 running，注入 input；**doom loop 檢查** |
| `tool-result` | 標記 tool 為 completed，存 output |
| `tool-error` | 標記 tool 為 error |
| `start-step` | 記錄 snapshot（git diff 基準點） |
| `finish-step` | 計算 token 用量和費用，觸發壓縮判斷，記錄 git diff patch |
| `finish` | 結束（不需特別處理） |

---

## Doom Loop 偵測

```
session/processor.ts:303
```

在每個 `tool-call` 事件，processor 會檢查：

```
最近 3 個 tool part 是否都是：
  - 相同 tool name
  - 相同 input（JSON 比對）
  - 都不是 pending 狀態

若是 → permission.ask("doom_loop") → 問使用者是否繼續
```

這是為了阻止 LLM 陷入無限重複同一個 tool call 的死循環。
使用者可以選擇繼續或中止。

---

## `LLM.stream()` 的實作

```
session/llm.ts
```

```
LLM.stream(streamInput: LLM.StreamInput):
  → 呼叫 Vercel AI SDK 的 streamText({
      model,
      system,
      messages,           ← 完整對話歷史（已轉成 LLM 格式）
      tools,              ← JSON Schema 格式的 tool 定義
      maxSteps: Infinity, ← 讓 SDK 自動處理 tool call → result 的多步
      abortSignal,
      ...provider 特定選項
    })
  → 把 SDK 的 stream events 轉成內部 LLM.Event 格式
  → 回傳 Effect Stream<LLM.Event>
```

Vercel AI SDK 在這裡屏蔽了所有 provider 差異：
- Anthropic → `tool_use` + `tool_result` 格式
- OpenAI → `function_call` + `function_call_result` 格式
- 其他 → 各自的格式

`streamText()` 會自動把 tool call → execute → result 的多輪對話包在一個 stream 裡，
不需要手動做這部分。

---

## `resolveTools()` 如何組出 tool 清單

```
session/prompt.ts:346
```

```
resolveTools({ agent, session, model, tools, processor, messages }):
  1. 取得 agent 定義中的 permission ruleset
  2. 呼叫 ToolRegistry.tools() 取得所有已註冊 tool
  3. 呼叫 MCP.tools() 取得所有 MCP server 提供的 tool
  4. 合併後，對每個 tool 做 permission filter：
     - permission.check(tool.name) → allow / deny / ask
     - deny → 從清單移除
     - ask → 包一層需要使用者確認的 wrapper
  5. 根據 model 能力過濾：
     - 模型不支援 image → 移除需要 multimodal 的 tool
  6. 若 lastUser.format.type === "json_schema" → 加入 StructuredOutput tool
  7. 回傳 Record<string, AITool>（Vercel AI SDK 的 tool 格式）
```

每一輪 loop 都重新建立 tool 清單，確保 permission 狀態是最新的。

---

## `insertReminders()` 注入了什麼

```
session/prompt.ts:210
```

每輪 loop 在呼叫 LLM 前，都會在 **lastUser message** 後面追加 synthetic text：

### 非 experimental mode

| 條件 | 注入內容 |
|------|---------|
| agent = plan | `plan.txt`：CRITICAL: Plan mode ACTIVE - READ-ONLY |
| 從 plan 切回 build（歷史中有 plan 訊息） | `build-switch.txt`：你現在可以改檔案了 |
| 其他 | 不注入 |

### experimental mode（experimental plan mode 開啟）

| 條件 | 注入內容 |
|------|---------|
| agent = plan，且這是第一次進入 plan | 完整 5 階段工作流程（Phase 1~5） |
| 從 plan 切回 build | BUILD_SWITCH + 計劃檔案路徑 |
| 其他 | 不注入 |

`synthetic: true` 的 part：
- 存在 DB 裡
- 傳給 LLM（LLM 看得到）
- **不顯示給使用者**（TUI 過濾掉 `synthetic === true` 的 part）

---

## 退出 Loop 的條件

```
runLoop() while(true) 有三個 break 點：

1. 正常結束：
   lastAssistant.finish 是 "stop"/"end-turn" 且沒有待執行的 tool call
   → break

2. process() 回傳 "stop"：
   - tool 被使用者拒絕（Permission.RejectedError / Question.RejectedError）
   - assistant message 有 error（provider 錯誤等）
   → break

3. structured output 完成：
   - lastUser 要求 JSON schema 格式輸出
   - StructuredOutput tool 被呼叫且成功
   → break
```

---

## 訊息格式轉換：`MessageV2.toModelMessagesEffect()`

DB 裡的訊息格式和 LLM API 要求的格式不同，每輪都要轉換：

```
DB 格式（MessageV2）:
  { role: "user", parts: [TextPart, FilePart, ToolPart, ...] }
  { role: "assistant", parts: [TextPart, ReasoningPart, ToolPart, ...] }

LLM 格式（Vercel AI SDK CoreMessage）:
  { role: "user", content: [{ type: "text", text: "..." }, { type: "image", ... }] }
  { role: "assistant", content: [{ type: "text", text: "..." }, { type: "tool-call", ... }] }
  { role: "tool", content: [{ type: "tool-result", toolCallId: "...", result: "..." }] }
```

轉換過程中：
- `synthetic: true` 的 part → 包含（LLM 看得到）
- `ignored: true` 的 part → 排除
- 圖片 part → 轉成 base64 image content
- tool call + tool result → 配對成 assistant tool-call + tool message

---

## 整體資料流圖

```
使用者輸入文字
      ↓
prompt() → createUserMessage() → DB
      ↓
runLoop() ─────────────────────────────────────────────────────┐
  │                                                             │
  │ 從 DB 載入訊息                                              │
  │ insertReminders() 注入 synthetic text                       │
  │ resolveTools() 組 tool 清單                                 │
  │ 建立 system prompt                                          │
  │ handle.process()                                            │
  │   │                                                         │
  │   │ llm.stream() → Vercel AI SDK streamText()              │
  │   │   ↓                                                     │
  │   │ LLM provider（Anthropic / OpenAI / ...）               │
  │   │   ↓                                                     │
  │   │ Stream<LLM.Event>                                       │
  │   │   ↓                                                     │
  │   │ handleEvent()：                                         │
  │   │   text-delta → DB updatePartDelta → TUI 即時顯示        │
  │   │   tool-call  → 執行 tool execute()                      │
  │   │     → permission check → 使用者確認（若需要）           │
  │   │     → 執行邏輯（read / write / bash / ...）             │
  │   │     → tool-result → DB 記錄輸出                        │
  │   │   finish-step → 計算 token / 費用 / git diff           │
  │   │                                                         │
  │   │ result: "continue" / "stop" / "compact"                │
  │   ↓                                                         │
  │ continue → 回到 loop 頂部，重新呼叫 LLM（帶 tool results）  │
  │ stop    → break，回傳最後的 assistant message               │
  │ compact → 壓縮 context，continue                           │
  └──────────────────────────────────────────────────────── ──┘
      ↓
回傳最後的 assistant message
```

---

## 和 Plan Mode 的對比

| 面向 | Build Mode | Plan Mode |
|------|-----------|-----------|
| Agent 名稱 | `build` | `plan` |
| 可寫入的檔案 | 任意 | 只有 `.opencode/plans/*.md` |
| Synthetic injection | build-switch（僅在從 plan 切回時） | READ-ONLY 提示（每輪） |
| Loop 結構 | 完全相同的 `runLoop()` | 完全相同的 `runLoop()` |
| 有什麼不同 | permission ruleset 不同 | permission ruleset 不同 |

**Plan mode 和 build mode 共用完全一樣的 agent loop 實作。**
差別只有 permission ruleset 和注入的 synthetic text。
