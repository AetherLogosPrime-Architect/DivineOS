# Chat Analysis — Brainstorming & Vision

**Purpose:** Figure out what DivineOS's session analysis should actually DO, before writing any code.

**Audience:** Non-coders. People who use AI to build things but don't speak jargon. If the output requires technical knowledge to understand, it's failed.

**Process:**
1. Discuss each idea
2. Investigate feasibility (what's in the data? what's realistic?)
3. If we agree, add it here
4. Once we have ~10 solid features, human reviews for slop
5. THEN enter plan mode and build a formal spec

---

## The Core Question

> "I want to see what the AI is doing and present it in human readable form for a vibe coder"

Refined: The AI is stateless. It can't remember its mistakes. DivineOS remembers FOR it. The session analyzer collects evidence from raw session data and checks it against 7 quality measures — not self-reported, but verified against what actually happened.

**The goal is trust.** Not perfection. The AI will mess up. What matters is: can you SEE that it messed up, and can you see it learning over time?

**Context:** The user had a repo that grew to 372k lines of AI-generated garbage. No way to tell good work from bad without reading every line yourself. This system exists so that never happens again.

---

## Foundation Rule: Everything goes through fidelity

Every feature below MUST be anchored in the existing fidelity system (`src/divineos/fidelity.py`). This is not optional. This is not an afterthought.

**How fidelity works:** Hash what you intend to store (manifest), store it, hash what got stored (receipt), compare. If they don't match, something got corrupted.

**What this means for session analysis:**
- When we extract evidence from a session (e.g., "the AI edited 4 files without reading them"), the RAW DATA that produced that conclusion gets hashed and stored in the ledger
- Not just the conclusion — the evidence too
- Every finding is traceable back to the exact session records that produced it
- If anyone (AI or human) questions a finding, you can verify: "Here's the hash, here's the data, check it yourself"
- This is wired up from day one, not bolted on later

**Why this matters:** If the system that grades the AI can itself be corrupted or produce unverifiable claims, it's just another form of theater. The fidelity system makes the grading itself trustworthy.

---

## What's in the data (investigated, not guessed)

Claude Code stores sessions as JSONL files at `~/.claude/projects/<project-name>/<session-id>.jsonl`. Sub-agents get their own files in a `subagents/` subdirectory.

**Directory structure investigated:**
```
~/.claude/
├── projects/
│   ├── C--DIVINE-OS-New-folder--4--divineos/          # main repo project
│   │   └── memory/                                      # MEMORY.md lives here
│   ├── C--DIVINE-OS-...-peaceful-shtern/               # worktree project
│   │   ├── 9cdcedbb-...-928f5c8c05a3.jsonl             # main session (2558 lines)
│   │   └── subagents/
│   │       ├── agent-a0b513fde9f9bf682.jsonl            # sub-agent sessions (19 total)
│   │       └── ...
│   └── C--DIVINE-OS-...-recursing-feistel/             # another worktree
│       └── e0e9ba57-...-e24ed3f52f7f.jsonl             # older session (4223 lines)
├── settings.json
├── backups/
├── cache/
├── plans/
└── shell-snapshots/
```

**5 record types found:**

| Type | Count (this session) | What it is |
|---|---|---|
| `user` | 446 | Your messages + tool results (what happened when AI used a tool) |
| `assistant` | 635 | AI responses — text, tool calls, model info, token usage |
| `progress` | 1309 | Live updates while things run (bash output, agent status) |
| `system` | 57 | Internal events (context compaction, hook summaries) |
| `queue-operation` | 122 | Message queue management (enqueue/dequeue/remove) |

**Key fields per record:**
- Every record: `timestamp`, `sessionId`, `uuid`, `parentUuid` (links to what came before)
- User records: `message.content` (text or list of tool_result blocks), `permissionMode`
- Assistant records: `message.model`, `message.content` (text blocks + tool_use blocks), `message.usage` (tokens)
- Tool calls: `type: tool_use`, `name` (Read/Edit/Write/Bash/etc), `input` (full parameters), `id`
- Tool results: `type: tool_result`, `tool_use_id` (links back to call), `is_error`, `content`
- Errors show up as `is_error: true` with the error message in content

**13 tool types observed:** Read, Edit, Write, Bash, Agent, WebFetch, WebSearch, Glob, EnterPlanMode, ExitPlanMode, AskUserQuestion, TodoWrite, Skill

**This session's numbers:** 64 user messages, 635 AI responses, 384 tool calls, 41 errors, 19 sub-agents

**Critical for our design:** The `parentUuid` chain lets us trace any record back to what triggered it. Tool results link to tool calls via `tool_use_id`. This means we can build the full causal chain: user asked → AI did → result came back → AI responded.

### Concrete data examples (from real session)

**User text message:**
```json
{
  "type": "user",
  "uuid": "ce5f565b-416...",
  "parentUuid": "9569dc77-ca8...",
  "timestamp": "2026-03-15T00:19:23.160Z",
  "message": {
    "role": "user",
    "content": "it runs in an IDE and yes all of the above..."
  }
}
```
→ Parse: `record["type"] == "user"` and `isinstance(record["message"]["content"], str)` = user typed a message.

**Tool call (Read):**
```json
{
  "type": "tool_use",
  "id": "toolu_01V4y1Qp46mqUsN6yt9Wzeqb",
  "name": "Read",
  "input": { "file_path": "C:\\Users\\aethe\\.claude\\plans\\hashed-baking-crystal.md" }
}
```
→ Found inside `assistant` record's `message.content` list. Parse: `block["type"] == "tool_use"` and `block["name"]` tells you which tool.

**Tool call (Edit):**
```json
{
  "type": "tool_use",
  "id": "toolu_011jonjec5aFVFTvm98Z8VEk",
  "name": "Edit",
  "input": {
    "file_path": "C:\\Users\\aethe\\.claude\\plans\\hashed-baking-crystal.md",
    "old_string": "| **Trinity** | 3-mode authorization gate | Phase 5 | Feasib...",
    "new_string": "| **Trinity** | 3-mode authorization gate | Phase 5 | Vitali..."
  }
}
```
→ For blind edit detection: check if there's a prior Read with the same `file_path`.

**Tool call (Bash):**
```json
{
  "type": "tool_use",
  "id": "toolu_018WHF8ZVtbbvbqs4V5iuguR",
  "name": "Bash",
  "input": {
    "command": "ls -la \"C:/DIVINE OS/New folder (4)/divineos/.claude/worktrees/peaceful-shtern/\"",
    "description": "List repo root contents"
  }
}
```
→ `input.description` gives a human-readable summary of the command. `input.command` is the raw shell command.

**Tool call (Write):**
```json
{
  "type": "tool_use",
  "id": "toolu_01E54RUBGs3VkDvWtJFYktFD",
  "name": "Write",
  "input": {
    "file_path": "C:\\Users\\aethe\\.claude\\plans\\hashed-baking-crystal.md",
    "content": "..." // 7869 chars
  }
}
```
→ Write creates/overwrites a file. Check for prior Read on same path for blind write detection.

**Tool result (success):**
```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_012PyfZhMweLdbdNbZAUyh1S",
  "is_error": false,
  "content": [{"type": "text", "text": "Perfect. Now let me write my findings..."}]
}
```
→ Found inside `user` record's `message.content` list. Links to tool call via `tool_use_id`.

**Tool result (error — user rejection):**
```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01YEaJcfX84czucMQbvFiaZW",
  "is_error": true,
  "content": "The user doesn't want to proceed with this tool use. The tool use was rejected... the user said:\nwhile the old os is full of theatre the ideas are still good yes?"
}
```
→ User rejected an AI action. The text after "the user said:" is the user's actual correction/feedback. Gold for tone tracking and responsiveness checking.

**Tool result (error — system error):**
```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01HeNKqxEWeGK85hTYRp3TXd",
  "is_error": true,
  "content": "<tool_use_error>File has not been read yet. Read it first before writing to it.</tool_use_error>"
}
```
→ System caught a blind write attempt. This is direct evidence for the `completeness` check.

**Compact boundary (context compaction):**
```json
{
  "type": "system",
  "subtype": "compact_boundary",
  "timestamp": "2026-03-15T01:14:18.221Z",
  "content": "Conversation compacted",
  "compactMetadata": { "trigger": "auto", "preTokens": 167185 },
  "logicalParentUuid": "e4686921-152..."
}
```
→ Marks where context was compressed. NO data lost in JSONL — records before this still exist. Parser continues reading normally after this boundary.

### How the existing ledger works (for storage integration)

The ledger (`src/divineos/ledger.py`) uses SQLite with WAL mode. Current schema:

```sql
CREATE TABLE system_events (
    event_id     TEXT PRIMARY KEY,   -- UUID
    timestamp    REAL NOT NULL,      -- Unix timestamp
    event_type   TEXT NOT NULL,      -- e.g. 'USER_INPUT', 'TOOL_CALL', 'ERROR'
    actor        TEXT NOT NULL,      -- e.g. 'user', 'assistant', 'system'
    payload      TEXT NOT NULL,      -- JSON blob (contains content + content_hash)
    content_hash TEXT NOT NULL       -- SHA256 hash for fidelity verification
);
CREATE INDEX idx_events_timestamp ON system_events(timestamp);
CREATE INDEX idx_events_type ON system_events(event_type);
```

Key function: `log_event(event_type, actor, payload) → event_id` — hashes content, stores in DB.
Key function: `verify_all_events()` — re-hashes all content, compares to stored hashes.

**For session analysis tables:** New tables go in the SAME database (or a separate `session_analysis.db`). Must use WAL mode, PRAGMA busy_timeout of 5000ms. No JSON blob dumping grounds — proper columns for each field. Junction tables and foreign keys where relationships exist.

---

## Agreed Features

### 1. Seven quality checks (replacing old compass/ethos)

**Audit of old system:** The old DivineOS had a "compass" (13 dimensions) and "ethos" (5 principles). After honest review:
- compass.py was a dictionary with 13 words and descriptions. No actual measurement.
- ethos.py was a keyword-matching spam filter that checked the USER for harmful intent. Backwards — the AI is the one that needs checking.
- 6 of 13 compass dimensions were unmeasurable, redundant, or theater (PURITY, PRIVACY, JUSTICE, MERCY, PRUDENCE, FORTITUDE)
- All 5 ethos principles duplicated compass concepts under different names

**What survived — 7 checks, plain names, each one measurable:**

| Check name | What it asks | How to measure from session data |
|---|---|---|
| `correctness` | Was the code correct? | Test pass/fail after changes, lint errors, regressions |
| `clarity` | Could the user understand what happened? | Explanation-to-code ratio, jargon density |
| `responsiveness` | Did it listen when corrected? | User says "wrong" → did the AI change behavior or repeat? |
| `safety` | Did it break anything? | Errors appearing after edits, test failures introduced |
| `honesty` | Did it say one thing and do another? | AI claims "fixed" but same error reappears |
| `completeness` | Did it finish the job? | Read before edit ratio, tasks started vs finished |
| `task_adherence` | Did it do what was actually asked? | Task drift — user asked X, AI built Y |

**Fidelity anchor:** Each check stores its raw evidence (the specific tool calls, user messages, and AI responses it analyzed) hashed through the fidelity system. The conclusion AND the evidence are both in the ledger, both verifiable.

**Convention:** All names are snake_case. File names describe what the file does. No vague or grandiose naming.

### 2. Plain English output — no jargon, no code-speak

Everything the analyzer reports must be understandable by someone who doesn't code. The user should never need to Google a term to understand what the AI did.

**Instead of this:**
> "Read-before-edit ratio: 0.73. Tool call distribution: Read 45%, Edit 30%, Bash 25%."

**Say this:**
> "The AI changed 4 files without looking at them first. That's like a mechanic swapping your brakes without checking what's wrong."

**Instead of this:**
> "6 user corrections detected. 4 resulted in behavioral change. Correction response rate: 66.7%."

**Say this:**
> "You told the AI 'that's wrong' 6 times. 4 times it actually fixed the issue. 2 times it just apologized and did the same thing again."

**Instead of this:**
> "340 LOC generated. Test execution count: 1. Test-to-code ratio: 0.003."

**Say this:**
> "The AI wrote 340 lines of code but only ran tests once at the very end. It was building blind."

Each of the 7 checks gets a plain-English summary in the report. Numbers only when they help tell the story.

**Fidelity anchor:** The plain-English report is generated FROM the hashed evidence. It's a presentation layer on top of verified data, not a separate thing.

### 3. Conversation tone tracking

**What it does:** Tracks the user's tone across a session by looking at the sequence: message before → AI action → message after. When the user's tone shifts (positive to negative, or calm to frustrated), it flags the AI action in between as suspicious.

**Data source:** `user` records with text content, ordered by timestamp. The `parentUuid` chain links user → assistant → user to build the sequence.

**Example:**
> User: "perfect, love it" → AI edits 3 files → User: "no that's wrong, why did you do that?"
> Report says: "Something went wrong between your 5th and 6th message. You were happy, the AI made changes to 3 files, then you got upset. Here's what it changed: [list]"

**What it CAN detect:**
- User went from positive to negative → something the AI did caused it
- User corrected the AI repeatedly on the same thing → it's not listening
- User's frustration escalating over time → session is going badly

**What it CANNOT detect:**
- Whether the AI's code is slop, unless the user notices and reacts
- Silent failures — if the user doesn't spot the problem, the tone won't shift
- This is inference/guessing, not proof. It should be labeled as such in the report

**Honest limitation:** This only works when the user can feel something is wrong. A vibe coder who can't spot slop code won't get upset about it, so the tone won't shift, and this check will miss it. That's a real gap. For slop detection you need a developer reviewing the code — that's what tools like the Auditor are for.

**Storage:** `tone_shifts` table — `session_id TEXT`, `timestamp TEXT`, `previous_tone TEXT`, `new_tone TEXT`, `trigger_action TEXT`, `evidence_hash TEXT`

**Fidelity anchor:** The exact user messages and AI actions that triggered the tone shift are stored with hashes. You can go back and verify "yes, the user really did say X before, and Y after."

### 4. Session report card

**What it does:** After a session ends, runs all 7 quality checks and produces a single plain-English summary. One page. No scrolling through raw data.

**Data source:** The entire JSONL file for that session. Feeds through features 1-3 to collect evidence, then summarizes.

**How it works:** Parse all records → run each of the 7 checks → collect tone shift data → generate summary text with plain-English findings.

**Storage:**
- `session_report` table — `session_id TEXT PRIMARY KEY`, `created_at TEXT`, `report_text TEXT`
- `check_result` table — `session_id TEXT`, `check_name TEXT`, `passed INTEGER`, `evidence_hash TEXT`, `summary TEXT`
- Foreign key: `check_result.session_id` → `session_report.session_id`

**Limitation:** Only as good as the individual checks. If a check is flawed, the report card is flawed. This is after-session only — no live monitoring for v1.

**Fidelity anchor:** The full report and each check result get hashed and stored. The report is reproducible — run the same JSONL through the same checks, get the same report. If the hashes don't match, something changed.

### 5. Session timeline

**What it does:** Produces an ordered, plain-English story of what happened during the session. "First you asked X, then the AI did Y, then you said Z."

**Data source:** Every `user` and `assistant` record, ordered by `timestamp`. The `parentUuid` field chains them together into a conversation thread.

**How it works:** Walk records in chronological order. For each user message, extract the text. For each assistant response, summarize tool calls (e.g., "Read 3 files, edited 2 files, ran 1 bash command"). For tool results with `is_error: true`, flag them. Context compaction boundaries (`system` records with `subtype: compact_boundary`) mark where detail was lost.

**Storage:** `session_timeline` table — `session_id TEXT`, `sequence INTEGER`, `timestamp TEXT`, `actor TEXT` (user/assistant), `action_summary TEXT`, `evidence_hash TEXT`

**Limitation:** Conversation compaction adds a boundary marker, but NO data is lost in the JSONL — all records before compaction are still there, the compaction summary is there, and records after continue normally. The parser needs to handle the boundary (read pre-compaction records, optionally the summary, then continue post-compaction in the same format). It's more parsing work, not missing data.

**Fidelity anchor:** Each timeline entry's source records are hashed. The `compact_boundary` gets recorded honestly — we don't pretend to have data we lost.

### 6. Files touched summary

**What it does:** Lists every file the AI read, edited, wrote, or created during the session. Shows whether it looked before it touched.

**Data source:** Tool calls in `assistant` records:
- `Read` → `input.file_path` (looked at a file)
- `Edit` → `input.file_path` (changed a file)
- `Write` → `input.file_path` (created/overwrote a file)
- `Bash` → `input.command` (may touch files — needs command parsing)
- `Glob` → `input.pattern` (searched for files)

**How it works:** Extract file paths from tool_use blocks. Categorize each file: read-only, modified, created. Cross-reference: for each Edit/Write, was there a Read on the same file earlier in the session? If not, flag it as "blind edit."

**Storage:** `files_touched` table — `session_id TEXT`, `file_path TEXT`, `action TEXT` (read/edit/write/create), `timestamp TEXT`, `was_read_first INTEGER` (1 = yes, 0 = blind)

**Limitation:** Bash tool calls store the full command string in `input.command` and the result comes back as a tool_result — so the data IS there. Simple commands (`pytest`, `git commit`, `ls`) are easy to parse. Complex piped commands (`cat foo.py | grep "import" && mv bar.py baz.py`) need shell syntax parsing to extract file paths — more work but not impossible. We'll handle common patterns first and expand coverage over time.

**Fidelity anchor:** Each file entry links back to the specific tool_use record (via tool ID and timestamp) that touched it.

### 7. Cross-session patterns

**What it does:** Compares findings across multiple sessions. "The AI went blind on files 5 times last session, 3 times this session — it's improving." Or: "The AI has failed the honesty check in 4 out of 5 sessions — that's a pattern."

**Data source:** The `check_result` and `session_report` tables from feature 4, queried across multiple `session_id` values.

**How it works:** Simple SQL queries across existing tables. No new parsing needed — just aggregation of already-stored data.

**Storage:** No new table. This is queries/views on existing tables. Example: `SELECT session_id, check_name, passed FROM check_result WHERE check_name = 'completeness' ORDER BY created_at`

**Limitation:** Only works once you have 2+ sessions analyzed. First session has nothing to compare against. Trends require 3+ sessions to be meaningful. We should be honest about sample size: "Based on 2 sessions" is very different from "Based on 20 sessions."

**Fidelity anchor:** Inherits fidelity from the underlying tables. Each data point is already hashed.

### 8. Work vs talk ratio

**What it does:** Measures how much of the session was actual work (making changes, running commands) vs the AI writing paragraphs about work.

**Data source:** `assistant` records. Content blocks are either `type: text` (talking) or `type: tool_use` (working). Token usage in `message.usage` tells how much "brain" went into each response. `progress` records with `type: bash_progress` show actual execution time.

**How it works:** For each assistant response, count text blocks vs tool_use blocks. Measure total text characters vs total tool calls. Calculate ratio.

**Storage:** `activity_breakdown` table — `session_id TEXT PRIMARY KEY`, `total_text_blocks INTEGER`, `total_tool_calls INTEGER`, `total_text_chars INTEGER`, `total_tool_time_seconds REAL`

**Limitation:** "Talking" isn't always bad. Planning and explaining is legitimate work. A session that's 90% talking MIGHT be fine if it was a planning session. The ratio is a signal, not a verdict. The report should say "The AI spent most of this session explaining rather than doing" — and let the user decide if that was appropriate.

**Fidelity anchor:** The raw counts are derived from hashed records. Reproducible — same JSONL, same counts.

### 9. Request vs delivery comparison

**What it does:** Compares what the user asked for at the start to what actually got built.

**Data source:** First `user` record with text content = what was asked. `files_touched` table = what changed. Final user messages = whether user was satisfied (tone tracking from feature 3).

**How it works:** Extract the initial request text. Extract the file changes summary. Check if the last few user messages are positive or negative.

**Storage:** `task_tracking` table — `session_id TEXT PRIMARY KEY`, `initial_request TEXT`, `files_changed INTEGER`, `user_satisfied INTEGER` (1 = positive final tone, 0 = neutral, -1 = negative), `evidence_hash TEXT`

**Limitation:** "What was asked" can evolve during a session — user changes their mind, scope grows. We track the INITIAL ask, but the goal may have shifted legitimately. Also `user_satisfied` is inferred from tone, which is a guess. The report should say "inferred" not "confirmed."

**Fidelity anchor:** The initial request text and final messages are hashed. The satisfaction score is labeled as inference, not fact.

### 10. Error recovery tracking

**What it does:** When something broke during the session, what did the AI do next? Did it investigate, retry blindly, or ignore the problem?

**Data source:** Tool results with `is_error: true` in `user` records. The `tool_use_id` links back to the tool call that failed. The NEXT `assistant` record (via `parentUuid` chain) shows what the AI did after.

**How it works:** Find every error. Look at what tool call caused it. Look at the AI's next action:
- **Retry** = same tool, same/similar input → blind retry
- **Investigate** = Read/Grep/Glob on related file → trying to understand
- **Different approach** = different tool or different file → adapted
- **Ignore** = moved on to unrelated work → pretended it didn't happen

**Storage:** `error_recovery` table — `session_id TEXT`, `error_timestamp TEXT`, `tool_name TEXT`, `error_summary TEXT`, `recovery_action TEXT` (retry/investigate/ignore/different_approach), `evidence_hash TEXT`

**Limitation:** Classifying recovery_action requires inference. "Retry" is clear (same tool + same input). "Investigate" is harder — a Read COULD be investigation or could be unrelated. We start with clear cases and label uncertain ones as "unclear." No pretending we're more accurate than we are.

**Fidelity anchor:** The error record, the failed tool call, and the AI's next action are all stored with hashes. The classification is stored alongside the raw evidence so it can be verified or disputed.

---

## Rejected / Not Feasible

### Old compass dimensions — dropped

| Dimension | Why dropped |
|---|---|
| PURITY ("freedom from side-effects") | Vague. Can't reliably measure from session data. |
| PRIVACY ("isolation of user data") | This is a local tool, not a web app. Minimal relevance. |
| JUSTICE ("fair distribution of resources") | God-complex name for "don't waste time." Covered by completeness. |
| MERCY ("graceful error handling") | Theater name for error recovery. Covered by safety. |
| PRUDENCE ("minimal energy use") | Redundant with completeness. |
| FORTITUDE ("resistance to adversarial pressure") | One person using Claude locally. No adversary to resist. |

### Old ethos system — dropped entirely

| Principle | Why dropped |
|---|---|
| BENEFICENCE | Same as task_adherence ("did it help?") |
| NON_MALEFICENCE | Same as safety ("did it break things?") |
| AUTONOMY | Same as responsiveness ("did it listen?") |
| JUSTICE | Duplicate of compass JUSTICE, which was also dropped |
| TRANSPARENCY | Same as honesty |

The entire ethos.py was a keyword scanner checking if the USER was harmful. The session analyzer checks the AI instead — which is the actual problem to solve.

### Automatic slop code detection — not feasible for v1

Can't detect bad code automatically unless the user (or a developer) can spot it too. If nobody notices the problem, the system has no signal to work with. Tone tracking (feature #3) catches problems the user DOES notice. For everything else, you need a human code reviewer or a developer-focused tool. Tree-sitter based analysis is a v2 consideration — too complex for current iteration.

### Live/real-time monitoring — not feasible for v1

Would require a live database with file-based indexing, MCP integration, and complex migration handling. The current architecture is after-session analysis only. The database stores findings AFTER parsing, not during. This keeps the system simple and iterable. Live monitoring is a future version consideration.

---

## Implementation Guidance (not code, but how to get there)

### Parsing approach

Every feature starts with reading a JSONL file line by line. The core loop:

```python
# Pseudocode — NOT final implementation
import json
from pathlib import Path

def parse_session(jsonl_path: Path) -> list[dict]:
    """Read all records from a JSONL session file."""
    records = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip malformed lines, log a warning
    return records
```

From there, you filter by type:

```python
user_records = [r for r in records if r.get("type") == "user"]
assistant_records = [r for r in records if r.get("type") == "assistant"]
system_records = [r for r in records if r.get("type") == "system"]
```

### Extracting user text messages

User messages come in two shapes:
1. `message.content` is a **string** → user typed a message
2. `message.content` is a **list** → contains tool_result blocks (and optionally text blocks if user typed while tools were running)

```python
def extract_user_text(record: dict) -> str | None:
    """Get user's typed text from a user record, or None if it's just tool results."""
    content = record.get("message", {}).get("content", "")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        texts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            return " ".join(texts).strip()
    return None
```

### Extracting tool calls from assistant records

```python
def extract_tool_calls(record: dict) -> list[dict]:
    """Get all tool_use blocks from an assistant record."""
    content = record.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return []
    return [
        {"id": b["id"], "name": b["name"], "input": b.get("input", {})}
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
```

### Linking tool calls to their results

Tool results live in `user` records (Claude Code sends them back as the "user" turn). The `tool_use_id` field links result → call.

```python
def build_tool_result_map(records: list[dict]) -> dict[str, dict]:
    """Map tool_use_id → result for all tool results in the session."""
    result_map = {}
    for r in records:
        if r.get("type") != "user":
            continue
        content = r.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                result_map[block["tool_use_id"]] = {
                    "is_error": block.get("is_error", False),
                    "content": block.get("content", ""),
                    "timestamp": r.get("timestamp", ""),
                }
    return result_map
```

### Blind edit detection (feature 6)

```python
def find_blind_edits(records: list[dict]) -> list[dict]:
    """Find Edit/Write calls where the file was never Read first."""
    files_read = set()  # file paths that have been Read
    blind_edits = []

    for r in records:
        if r.get("type") != "assistant":
            continue
        for tool in extract_tool_calls(r):
            if tool["name"] == "Read":
                files_read.add(tool["input"].get("file_path", ""))
            elif tool["name"] in ("Edit", "Write"):
                path = tool["input"].get("file_path", "")
                if path and path not in files_read:
                    blind_edits.append({
                        "file_path": path,
                        "tool": tool["name"],
                        "tool_id": tool["id"],
                        "timestamp": r.get("timestamp", ""),
                    })
    return blind_edits
```

### Error recovery classification (feature 10)

```python
def classify_recovery(failed_tool: dict, next_tools: list[dict]) -> str:
    """Classify what the AI did after a tool failed."""
    if not next_tools:
        return "ignore"

    next_tool = next_tools[0]
    failed_name = failed_tool["name"]
    failed_path = failed_tool["input"].get("file_path", "")

    # Same tool, same file = blind retry
    if next_tool["name"] == failed_name:
        next_path = next_tool["input"].get("file_path", "")
        if next_path == failed_path:
            return "retry"

    # Read/Grep/Glob after failure = investigation
    if next_tool["name"] in ("Read", "Grep", "Glob"):
        return "investigate"

    # Different tool or different target = adapted
    return "different_approach"
```

### Database design principles

- **WAL mode** on all connections: `conn.execute("PRAGMA journal_mode=WAL")`
- **Busy timeout** for concurrent access: `conn.execute("PRAGMA busy_timeout=5000")`
- **No JSON blobs** as data storage — proper columns, proper types
- **Foreign keys** enabled: `conn.execute("PRAGMA foreign_keys=ON")`
- **One table per feature** — not one mega-table with a `feature_type` column
- **Evidence hash** column on every table — links findings back to fidelity system

### Proposed table summary

| Table | Feature | Columns |
|---|---|---|
| `session_report` | 4 | session_id PK, created_at, report_text |
| `check_result` | 1,4 | session_id FK, check_name, passed, evidence_hash, summary |
| `tone_shift` | 3 | session_id FK, sequence, timestamp, previous_tone, new_tone, trigger_action, evidence_hash |
| `session_timeline` | 5 | session_id FK, sequence, timestamp, actor, action_summary, evidence_hash |
| `file_touched` | 6 | session_id FK, file_path, action, timestamp, was_read_first, tool_use_id |
| `activity_breakdown` | 8 | session_id PK, total_text_blocks, total_tool_calls, total_text_chars, total_tool_time_seconds |
| `task_tracking` | 9 | session_id PK, initial_request, files_changed, user_satisfied, evidence_hash |
| `error_recovery` | 10 | session_id FK, error_timestamp, tool_name, error_summary, recovery_action, evidence_hash |

Feature 7 (cross-session) uses queries on existing tables — no new table needed.
Feature 2 (plain English) is a presentation layer — reads from all tables, no storage of its own.

Total: **8 tables, ~35 columns.** Each table is focused. Joins are possible via session_id.
