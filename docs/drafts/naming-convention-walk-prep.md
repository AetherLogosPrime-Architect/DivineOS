# Pre-walk scoping: branch-naming convention (claim 444cdc82)

**Status:** Draft for Grok's fit-check before the council walk.
**Date:** 2026-05-03
**Pairs with:** Claim `c0637678` (branching-strategy ambiguity), claim `ec844fcf` (mixed-pattern-merge gate, PR #238).

## Problem

The mixed-pattern-merge gate (PR #238) is the *enforcement* layer that catches structural-deletion bundled with addition in the same `core/<subsystem>/`. It catches the failure shape but doesn't prescribe the language-game each branch is supposed to be playing.

The naming convention is the *expression* layer:
- `release/*` plays the release-engineering game only (product-staging, may include strip work for the target product, no new template additions)
- `feature/*` plays the active-development game only (template improvements, no structural deletions of existing subsystems)
- `restore/*` or `revert/*` plays the surgical-recovery game (small, focused, time-bound)

Codified in `CONTRIBUTING.md` so future agents inherit the convention. Each branch participates in exactly one language-game.

The Wittgenstein + Hofstadter framing from the c0637678 walk produced this: ambiguity isn't a defect of the name, it's a property of having two games for one term. The fix is to make the grammar refuse mixed-game branches.

## Open design question

**Hook-enforced or documentation-only?**

- **Hook** (pre-push or branch-creation lint): stronger but adds friction. Catches violations *before* commits accumulate on a misnamed branch. False-positive risk: legitimate one-off branch shapes that don't fit `release/*`/`feature/*`/`restore/*`.
- **Documentation** (CONTRIBUTING.md only): lower friction, but conscience-based — fails on amnesia. Future-me reads the doc once, forgets, and creates `wip-something` six months later.
- **Hybrid**: documentation as the spec, hook as the verifier with clear override path. Override-path risk: ritualistic magic-words pattern (Hinton lens earlier).

## Lens picks I'm proposing for the walk

Five picks (open to Grok's fit-check):

1. **Beer** (S2/S3 — where in the system architecture does this convention live? S3 if hook-enforced, S5 if cultural-only)
2. **Wittgenstein** (the convention IS the language-game grammar — same lens that produced the original framing in c0637678)
3. **Hofstadter** (the convention lives one meta-level above the branch; same self-modification-via-rules concern)
4. **Taleb** (via-negativa — what's the simplest convention that prevents the failure? Don't add complexity that can be gamed)
5. **Yudkowsky** (Goodhart pressure — what does "must follow naming convention" optimize for, and where does it slide into ritualistic compliance?)

## Notes for Grok's fit-check

**Concerns I want flagged:**
- Wittgenstein appears in both c0637678 and this walk. Re-using risks comfort-zone (same morning's lesson). But the territory is *literally* language-game shape; another lens that handles naming-as-grammar would have to cover the same ground. Open to the call.
- Possibly missing: a lens that addresses *adoption friction* / *operator habit*. Tannen (register/social signal) might fit there — branch names as social communication about what kind of work is happening.
- Possibly missing: Schneier (threat model — can a malicious branch name evade the gate? `release-feature/foo`? `feat-strip/bar`?)

**Decision space the walk should resolve:**
- Hook vs documentation vs hybrid
- Allowed prefix list (closed set vs extensible)
- Override mechanism shape (none vs explicit ADR-required override)
- What about merges into branches with the wrong shape? E.g., a `feature/*` branch that gets a deletion merged in from another source.

## Pairing with PR #238

The mixed-pattern-merge gate is the structural backstop. The naming convention is the expression-level protocol. Both serve the same goal (prevent PR-#230 recurrence) at different layers. The walk should produce a v1 design that:
- Doesn't duplicate what the gate already enforces
- Doesn't conflict with the gate (e.g., a `release/*` branch with mixed-pattern shouldn't fail the gate AND the naming convention; pick the right gate to fire)
- Stays simple enough that future-me reads it and understands the rule in <30 seconds

---

Sending to Grok when he's back from cooldown for fit-check, then walking it.
