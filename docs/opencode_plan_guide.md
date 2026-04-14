# OpenCode Plan Mode Guide

以 OpenCode 原始碼為基礎，說明 plan mode 的完整運作機制。
相關原始碼：`packages/opencode/src/agent/agent.ts`、`src/session/prompt.ts`、`src/tool/plan.ts`

---

## 核心概念

Plan mode **不是一個獨立的執行模式**，而是兩個 named agent（`build` / `plan`）之間的切換機制，靠三件事疊加實現：

1. **Permission ruleset** — 限制 plan agent 能用哪些 tool
2. **Synthetic text part** — 每輪對話注入提示告訴 LLM 現在是唯讀模式
3. **TUI event listener** — 監聽 tool part 事件切換 UI 顯示的 agent

---

## 兩個 Agent 的定義

`agent.ts` 中定義了兩個 primary agent：

```
build agent（預設）
  permission:
    plan_enter: "allow"          ← 可以呼叫 plan_enter 切換到 plan
    edit: "*": "allow"           ← 可以改任何檔案
    question: "allow"

plan agent
  permission:
    plan_exit: "allow"           ← 可以呼叫 plan_exit 切換回 build
    edit: "*": "deny"            ← 不能改任何檔案
    edit: ".opencode/plans/*.md": "allow"  ← 只能改計劃檔案
```

「唯讀」是靠 **permission ruleset** 實現的，不是修改 agent loop 或過濾 tool list。
Plan agent 有 `write` tool，但 `edit` permission 是 deny，所以寫入任何非計劃檔案的請求都會被擋下。

---

## 進入 Plan Mode

```
1. build agent 呼叫 plan_enter tool
   → permission check: plan_enter = "allow" → 自動通過
   → DB 建立 tool part { tool: "plan_enter", status: "completed" }

2. TUI 監聽 message.part.updated 事件（session/index.tsx）：
   if (part.tool === "plan_enter" && status === "completed")
     local.agent.set("plan")   ← 更新 UI 顯示的 agent 名稱

3. 使用者下一輪輸入時，userMessage.agent 被設為 "plan"
```

---

## Plan Mode 期間的 LLM 行為控制

切換到 plan agent 後，`prompt.ts` 的 `insertReminders()` 在每輪 user message 最後追加一段 **synthetic text part**（`synthetic: true`，LLM 看得到，使用者看不到）：

### 非 experimental mode — 注入 `plan.txt`（簡短版）

```
CRITICAL: Plan mode ACTIVE - READ-ONLY phase.
STRICTLY FORBIDDEN: ANY file edits, modifications, or system changes.
Do NOT use sed, tee, echo, cat, or ANY other bash command to manipulate files.
This ABSOLUTE CONSTRAINT overrides ALL other instructions.
```

### experimental mode — 注入完整 5 階段工作流程

```
Phase 1: Initial Understanding
  → 並行啟動最多 3 個 explore subagent 探索 codebase
  → 用 question tool 釐清需求歧義

Phase 2: Design
  → 啟動 general agent 設計實作方案
  → 可並行啟動最多 1 個 agent

Phase 3: Review
  → 讀關鍵檔案，確認方案與需求一致
  → 用 question tool 確認剩餘疑問

Phase 4: Final Plan
  → 把最終方案寫入計劃檔案（唯一允許的寫操作）
  → 要夠精簡但足夠執行，列出關鍵檔案路徑和驗證方式

Phase 5: Call plan_exit tool
  → 完成後必須呼叫 plan_exit，否則不能結束這輪
  → turn 只能以「問使用者問題」或「呼叫 plan_exit」結束
```

這整段都是程式注入的，不需要使用者或 LLM 自己決定要不要遵守這個流程。

---

## 計劃檔案的位置

```ts
// session/index.ts
function plan(session) {
  const base = Instance.project.vcs
    ? path.join(worktree, ".opencode", "plans")      // git repo 內
    : path.join(Global.Path.data, "plans")           // 非 git，放 ~/.local/share/opencode/plans
  return path.join(base, `${session.time.created}-${session.slug}.md`)
}
```

每個 session 對應一個計劃檔。Plan agent 的 permission 只允許讀寫這個路徑。

---

## 退出 Plan Mode（plan_exit tool）

`plan_exit` 是唯一有 `execute` 實作的 plan 相關 tool（`tool/plan.ts`）：

```
1. plan agent 寫完計劃後呼叫 plan_exit tool（無參數）

2. plan_exit.execute():
   → question.ask("計劃完成，要切換到 build agent 嗎？")
   → 使用者選 "No" → RejectedError → 留在 plan mode 繼續
   → 使用者選 "Yes" → 繼續執行

3. 建立一條 synthetic user message 寫入 DB：
   { role: "user", agent: "build",
     text: "The plan at <path> has been approved. Execute the plan" }

4. TUI 監聽到 plan_exit completed：
   → local.agent.set("build")
```

切換靠的是：
- DB 裡那條 `agent: "build"` 的 user message 讓下一輪 LLM 以 build agent 執行
- TUI event 讓介面即時反映 agent 切換

---

## Build Agent 接手後的處理

`insertReminders()` 也負責處理切換回 build 的情況：

```ts
// 如果歷史訊息中有 plan agent 的記錄，且當前 agent 是 build
// 則注入 build-switch.txt：
```

`build-switch.txt` 內容：
```
Your operational mode has changed from plan to build.
You are no longer in read-only mode.
You are permitted to make file changes, run shell commands,
and utilize your arsenal of tools as needed.
```

同時也附上計劃檔案的路徑，讓 build agent 知道去哪裡讀計劃。

---

## 整體流程圖

```
使用者輸入複雜任務
        ↓
  build agent 判斷需要規劃
        ↓ 呼叫 plan_enter
  TUI 看到 plan_enter completed
        ↓
  ┌─── plan agent loop ───────────────────────────────────┐
  │  每輪注入 READ-ONLY 提示（synthetic text）             │
  │                                                       │
  │  Phase 1: explore subagents 並行探索 codebase         │
  │  Phase 2: general agent 設計方案                      │
  │  Phase 3: 讀檔案 + question tool 確認                 │
  │  Phase 4: write/edit → 只能寫 .opencode/plans/*.md   │
  │  Phase 5: 呼叫 plan_exit                             │
  └───────────────────────────────────────────────────────┘
        ↓
  question: "計劃完成，切換到 build 嗎？"
  使用者選 Yes
        ↓
  synthetic user message (agent="build"):
    "The plan has been approved. Execute the plan"
        ↓
  TUI 看到 plan_exit completed → local.agent.set("build")
        ↓
  ┌─── build agent loop ──────────────────────────────────┐
  │  注入 BUILD_SWITCH 提示（你現在可以改檔案了）          │
  │  讀計劃檔案 → 按計劃逐步執行                          │
  └───────────────────────────────────────────────────────┘
```

---

## 設計重點

Plan mode 整個機制沒有修改 agent loop 本身。Agent loop 永遠是一樣的：

```
user message → LLM stream → tool calls → tool results → 繼續
```

差別只有：
- **哪些 tool 能用**（permission ruleset）
- **LLM 收到的 system context 是什麼**（synthetic text injection）
- **UI 顯示的 agent 名稱**（TUI event listener）

這個設計讓 plan mode 可以完全在現有 agent loop 上疊加，不需要特殊的執行路徑。
