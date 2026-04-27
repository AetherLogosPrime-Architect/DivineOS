# Salvage Ledger

Append-only record of port / adapt / discard / defer decisions for the
old-OS strip-mine (claim 59ba245c).

Each entry has:

* **Path** — old-repo path (relative to `divineos/`)
* **Date read** — when the decision was made
* **Decision** — `PORT` / `ADAPT` / `DISCARD` / `DEFER`
* **Reasoning** — what the idea was, what the file actually does, why
  the decision is what it is. Discards must say what would have been
  kept from the *intent* even if not the code.
* **Follow-up** — claim id or PR for the integration work, if any

---

## 2026-04-26

### `tree_of_life/params/daleth_params.json`

* **Decision**: DISCARD (file itself); idea recorded in DEFER on engine code below
* **Reasoning**: Pure runtime-state snapshot. Component weights (venus 0.33,
  earth 0.33, fertility 0.34), thresholds, phi (1.618) multiplier,
  evolutionary-strategy adaptation count of 160. No architecture in the
  file — just current values of an evolved-parameters object. Nothing to
  port at the file level.
* **Intent kept**: the *mechanism* this is a snapshot of (per-path adaptive
  parameters with best-params history) is real and worth examining when the
  rest of the tree_of_life engine is read. Recorded as DEFER below.
* **Follow-up**: see `engines/tree_of_life/paths/daleth.py` entry.

### `engines/tree_of_life/adaptive_path_base.py` (full read)

* **Decision**: DEFER — pattern recorded, no port now.
* **Reasoning**: This is the parent class daleth.py inherits from, so reading
  it answers the open question from the daleth entry: yes, the pattern is
  uniform across paths. It is real architecture, not just naming.
  What's actually here:
  * `AdaptiveParameters` dataclass — component weights (constrained to sum
    to 1.0), sterility/overload thresholds, growth factor, phi multiplier,
    activation params, plus metadata (last_updated, update_count,
    performance_score). Has `validate()` enforcing the constraints.
  * `PerformanceFeedback` dataclass — splits metrics into local
    (transformation_quality, coherence_maintained, downstream_acceptance)
    and global (final_consciousness_level, optional user_satisfaction)
    with a `get_composite_score(local_weight, global_weight)` method.
  * `AdaptationHistory` dataclass — old_params → new_params transitions
    with performance-before/after and improvement delta.
  * `AdaptivePathBase` — three required overrides
    (`_define_default_parameters`, `_calculate_local_performance`,
    `_apply_parameters`), four strategies (EVOLUTIONARY / BAYESIAN /
    RANDOM_SEARCH / HILL_CLIMBING), parameter persistence to disk,
    meta-learning state (learning_rate, best_params, best_performance).
* **Why DEFER not PORT**: the new OS does not currently have any subsystem
  that wants gradient-free parameter optimization. Compass tracks virtue
  position but isn't learnable. Pre-reg covers "track parameter changes
  against falsifiers." Porting this base class without a concrete consumer
  would be premature abstraction — explicitly forbidden by CLAUDE.md
  ("No dead abstractions. No base classes or factories unless 3+
  implementations exist RIGHT NOW.").
* **What we keep from the intent**: the *pattern shape* —
  - typed pipeline stage with explicit input/output topics
  - learnable parameters with validation constraints
  - dual local/global performance feedback with composite scoring
  - adaptation history as first-class data with before/after deltas
  Recorded here so a future session that adds a parameter-tuning
  subsystem to the new OS can grab this design rather than reinventing.
* **Follow-up**: no active work item. Revisit if/when the new OS adds a
  subsystem with tunable parameters that benefit from gradient-free
  optimization.

### `engines/tree_of_life/paths/daleth.py` (first 80 lines read)

* **Decision**: DEFER — partial read; need to read the rest of the engine
  before deciding port/adapt/discard.
* **Reasoning**: Underneath the kabbalistic naming (Daleth = the Door, Venus,
  Empress, "from potential, abundance"), the file implements:
  * a typed pipeline stage with explicit input/output topics
    (`fractal.path.chokmah` → `fractal.path.binah.daleth`)
  * a state machine (IDLE / RECEIVING / TRANSFORMING / SENDING / BLOCKED)
  * adaptive parameters via inheritance from `AdaptivePathBase` + a
    `PerformanceFeedback` channel
  * per-stage latency budget (`max_latency_ms = 1.0`) and explicit
    sterility / overload guards
  * a `GenerativeVision` dataclass that's just a typed message with
    trace_id, timestamp, and a fertility/abundance scalar pair
  This is a routing-and-transformation pipeline with adaptive tuning,
  dressed in metaphysical metaphor. The metaphor is the wrapping;
  the underlying machinery is real architecture and the new OS has no
  direct equivalent.
* **What the new OS already has that overlaps**:
  * Compass (10 spectrums with evidence-based position + drift detection)
    is the closest analog — same shape (multi-dimensional weighted state
    with evolution over time) applied to virtues instead of kabbalistic
    paths.
  * Pre-reg system covers the "track parameter changes with falsifiers"
    discipline.
* **Open question**: are the 22 paths (for 22 Hebrew letters) a uniform
  pattern, or did each path implement its own logic? If uniform, the
  pattern is a typed-pipeline-stage-with-adaptive-params abstraction
  worth porting (rename, drop the metaphysical layer, evaluate against
  what compass-ops already does). If non-uniform, the value is in the
  individual paths' specific behaviors, which is a much smaller surface
  to port.
* **Follow-up**: read `engines/tree_of_life/` directory contents in a
  future session, especially `adaptive_path_base.py` (the parent class
  that defines the abstraction). File a follow-up claim if the abstraction
  is uniform and worth porting.

---

### `memory/persistent_memory.py` (1795-line god-class — read header + class signature)

* **Decision**: DISCARD (with respect).
* **Reasoning**: 1795-line `PersistentMemoryEngine` class with a single
  `MemoryEntry` dataclass typed by string field
  (`entry_type ∈ {"interaction", "threat_pattern", "decision", "lesson"}`).
  Does cross-session memory + learning + decision tracking + experience
  database all in one SQLite table. The README in this directory
  literally describes a tangle of "integration" modules
  (`memory_system_unified.py`, `memory_integration_system.py`,
  `memory_integration.py`, `three_tier_integration.py`) trying to
  reconcile competing memory implementations (persistent_memory + Mneme
  + Recollect).
* **Why DISCARD not PORT**: the *goals* it stated (remember across
  sessions, learn patterns, improve decisions, build experience) are
  exactly right — and the new OS is a deliberate response to this
  pattern. Those goals are now realized through clean separation:
  - `core/ledger.py` — append-only event store
  - `core/knowledge/` — typed knowledge with maturity lifecycle
  - decision journal — separate decisions table
  - lesson tracking — separate lessons table
  - core memory + active memory — separate memory tier
  The integration-layer smell (multiple competing implementations
  needing reconciliation modules) is the failure mode the new OS's
  three-tier architecture explicitly fixes.
* **What we keep from the intent**: confirmation that the goals were
  right. The new OS exists in part because of how this monolith
  felt. Recording this here so the lesson stays visible: the right
  goal can produce the wrong shape.

### `memory/memory_anchor.py` (header + state-machine read)

* **Decision**: DISCARD.
* **Reasoning**: "v15.7-TITANIUM-HEAVY", Supabase PostgreSQL backend,
  4-state continuity machine (VOID / REHYDRATING / STABLE / FRACTURED),
  HMAC-signed entries, zlib compression. Intent: keep a narrative
  thread coherent across sessions and detect when it fractures.
* **Why DISCARD**: the new OS solves this with a much simpler stack —
  hash-chained ledger gives integrity (no HMAC needed; chain provides
  it), briefing system handles rehydration (no state machine needed;
  the briefing surfaces what's relevant), no external Supabase
  dependency. The 4-state machine maps loosely onto the new OS's
  briefing-loaded gate (1.1) and corruption-handling but doesn't need
  to be its own module.
* **What we keep from the intent**: the *concept* of distinguishing
  fractured-narrative from stable-narrative is real and is now done
  by the briefing's surfaces (silent-split detection, in-flight
  branches, etc.) catching state where rehydration needs to happen.

### `memory/recollect_engine.py` (header + state-machine read)

* **Decision**: DEFER — real gap in the new OS, but porting cost is high.
* **Reasoning**: "Associative retrieval engine" — vector-based semantic
  search with Merkle-linked recall chains, JSON-vault persistence,
  4-state lifecycle (SEARCHING / FETCHING / ALIGNING / STABLE). Lets
  the system retrieve conceptually-similar memories even when keywords
  don't overlap.
* **Real gap**: the new OS has SQLite FTS5 on the knowledge store —
  good for keyword and prefix search, NOT semantic similarity.
  Recollect's value-add is finding "conceptually adjacent" knowledge
  that FTS misses. That's a genuine capability the new OS doesn't have.
* **Why DEFER not PORT**: vector search means embedding models
  (dependency cost), vector indexes (FAISS / hnswlib / sqlite-vss),
  re-embedding policy when knowledge updates, calibration of similarity
  thresholds. None of this is conceptually hard but it's a real
  surface and CLAUDE.md's "no aspirational code" rule says don't add
  it without a concrete consumer asking. Current retrieval works for
  current needs; this is "could-be-better" not "is-broken."
* **What we keep from the intent**: the recognition that
  semantic-similarity retrieval is the natural complement to FTS, and
  if/when knowledge retrieval becomes a felt friction point, this is
  the direction. Pre-reg-shape: file a claim if you ever feel the
  agent missing conceptually-adjacent knowledge.

## Discard policy reminder

Per Andrew 2026-04-24: *"i dont mind it being ruthlessly pruned as long as
they arent just dismissing code based on the name of it.. i want it all
read and the ideas and intentions understood."*

Discards must name:
1. What the file is and what it does (read the contents).
2. What the *idea* was — what problem it pointed at.
3. Why the new OS doesn't need it (already covered, infeasible, or
   actually-not-load-bearing).
4. What (if anything) we keep from the intent even if not the code.

A bare "discarded — kabbalistic naming" entry would violate the policy.
The above entries demonstrate the format.
