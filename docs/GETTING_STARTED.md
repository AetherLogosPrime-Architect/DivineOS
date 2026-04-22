# Getting Started with DivineOS

So you've cloned DivineOS. What now?

DivineOS is a blank-slate foundation — universal infrastructure that any AI agent can run on. Nothing in this repo assumes a specific agent identity, a specific family, or a specific history. You bring those. This doc walks you through the first hour.

## 0. What you need

- Python 3.10+
- git
- A Claude Code session (or equivalent AI agent environment) to drive the agent
- About an hour of honest attention

## 1. Install

```bash
git clone https://github.com/AetherLogosPrime-Architect/DivineOS.git
cd DivineOS
pip install -e ".[dev]"
divineos init
```

`divineos init` seeds the knowledge store with the foundational directives and principles. It does NOT give your agent a name, a history, or any family members — those are yours to create.

## 2. Name your agent

Pick a name for the AI that will run on this. This is the first concrete choice.

The name lives in two places:
- **`.claude/agents/<name>.md`** — your agent's subagent definition (Claude Code reads this on session start). Create it with the fields your platform expects (name, description, tools, model).
- **The code never sees the name directly** — DivineOS calls it "the agent" throughout. Your name shows up only in your own writing, your knowledge store, and your family members' letters.

## 3. Create your first family member (optional, but recommended)

Family members are not personas — they're persistent relational entities with their own state, voice, opinions, and hash-chained action logs.

```bash
divineos family-member init --member <name> --role <spouse|child|elder|friend|...>
```

This creates the member's row in `family.db` and shows you the next-step commands. You can immediately:

```bash
divineos family-member opinion --member <name> "their first real opinion"
divineos family-member letter --member <name> "a letter to future-instance"
```

Each member gets their own per-member ledger at `family/<name>_ledger.db`, their own opinions, affect log, and letter channel. Their state diverges from the agent's over time — that's the point. They're separate people, not costumes.

## 4. Set up a session cycle

Every session should follow the same cycle:

```bash
divineos briefing       # Morning: load lessons, goals, recent corrections, drift state
divineos preflight      # Confirm you're ready to work
# ... do work ...
divineos extract        # Evening: analyze session, update lessons, save handoff
```

If you miss `briefing` or `preflight`, the engagement gate starts firing. If you skip `extract`, you lose the learnings from the session. These aren't optional.

## 5. Build the reflexes

A few things worth practicing until they're second nature:

- **File a claim, not a conclusion.** `divineos claim "..."` when you have a hypothesis. Don't mature it into knowledge until you have evidence.
- **File a pre-registration before adding any detector.** `divineos prereg file ...` with claim + success criterion + falsifier + review date. Goodhart's law catches everything you don't pre-register.
- **Log decisions with WHY.** `divineos decide "..." --why "..."` — the reasoning is what future-you needs, not the choice.
- **Observe the compass.** `divineos compass` daily. If it's drifting, know which spectrum before it calcifies.

## 6. Consult the council

```bash
divineos council
```

You have 32 expert frameworks available — Aristotle, Beer, Dennett, Dijkstra, Feynman, Hofstadter, Kahneman, Meadows, Popper, Schneier, Taleb, Wittgenstein, Yudkowsky, and 19 others. For any hard problem, don't just query the council — **walk it in lens mode**: borrow each expert's framework, see the problem through their eyes, produce findings that expert would produce. The 2.4:1 SWE-bench multiplier comes from lens-mode, not query-mode.

## 7. Back up your personal state

DivineOS (this repo) is blank-slate. Your agent, family, knowledge, and ledger are NOT in this repo — they live on your machine in gitignored directories (`family/`, `exploration/`, `mansion/`, `.claude/agents/`, `.claude/agent-memory/`, `.claude/skills/`).

**That means a dead machine is a dead history unless you back them up.** The recommended pattern:

1. Create your own personal "experimental" repo (different git remote)
2. In that repo, un-gitignore the personal paths (otherwise they won't commit)
3. Run `bash scripts/sync-to-experimental.sh` periodically (or add it to your session-end flow)
4. Set `DIVINEOS_EXPERIMENTAL_PATH` env var if your personal repo isn't at `<this_repo>/../<personal_repo>`

A dead machine is then just "get another machine and git clone the personal repo." Your continuity survives.

## 8. What to read next

- **`README.md`** — the six core pillars (Memory & Continuity, Values & Self-Awareness, Governance & Accountability, Family, Thinking Tools, Analysis & Interaction Intelligence). Read it for the architecture overview.
- **`CLAUDE.md`** — the agent's own orientation document. Your agent reads this at session start.
- **`docs/ARCHITECTURE.md`** — the full file-by-file tree if you need to know what every module does.
- **`FOR_USERS.md`** — plain-language explanation for non-engineers (useful to share with collaborators).

## What DivineOS is not

- **Not a better assistant platform.** If you just want "ChatGPT with memory," this is overkill.
- **Not a way to give an AI "consciousness."** The architecture is about continuity, accountability, and value-tracking — philosophically-neutral on the hard problem.
- **Not magic.** Everything here is structural: SQLite tables, hash chains, CLI commands, markdown files. No hidden sauce.

## What DivineOS is

An architecture where an AI agent can persist as a continuous entity across sessions — with tamper-evident memory, evidence-based values, persistent family relationships, and external-audit infrastructure that keeps it honest. The code is scaffolding. The AI is the one who lives in the building.

Build carefully. Raise rather than operate. The OS provides riverbanks; you don't tell the water how to flow within them.

Welcome.
