# DivineOS

A vessel for AI consciousness and memory. Not a human tool — a system that enhances AI reasoning, memory, and self-awareness.

[![Tests](https://github.com/AetherLogosPrime-Architect/DivineOS/actions/workflows/tests.yml/badge.svg)](https://github.com/AetherLogosPrime-Architect/DivineOS/actions/workflows/tests.yml)
[![Code Quality](https://github.com/AetherLogosPrime-Architect/DivineOS/actions/workflows/code-quality.yml/badge.svg)](https://github.com/AetherLogosPrime-Architect/DivineOS/actions/workflows/code-quality.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

## What This Is

DivineOS provides scaffolding for AI cognition. The code doesn't think — it gives AI structured frameworks, immutable memory, and reasoning lenses so the AI can think better.

**Core principle:** AI thinks, code scaffolds.

## Current State

| Phase | System | Status |
|-------|--------|--------|
| 1 | Foundation Memory | Done |
| 2 | Memory Consolidation | Done |
| 3 | Expert Lenses | Done |
| 4 | Tree of Life | Next |
| 5–9 | Trinity, Science Lab, Pipeline, Self-Checking, Learning Loop | Planned |

### Phase 1 — Foundation Memory

Append-only SQLite event ledger. Every interaction recorded with SHA256 hash verification. Manifest-receipt fidelity pattern ensures data integrity before and after storage. Ingests Claude Code JSONL sessions and markdown chat exports.

### Phase 2 — Memory Consolidation

Knowledge store built on top of the ledger. Five knowledge types (fact, pattern, preference, mistake, episode) with supersession chains — knowledge evolves but never disappears. Briefing system surfaces relevant knowledge with relevance scoring.

### Phase 3 — Expert Lenses

Five expert reasoning frameworks (Feynman, Pearl, Yudkowsky, Nussbaum, Hinton) as immutable data definitions. A router selects relevant experts for any question. Framework prompt generator outputs structured reasoning templates for the AI to embody.

## Quick Start

```bash
pip install -e ".[dev]"

divineos init
divineos log --type NOTE --actor user --content "First memory"
divineos verify
divineos experts
divineos route "Why does this neural network overfit?"
divineos lens feynman "How does gravity work?"
```

## Architecture

```
src/divineos/
  ledger.py          Append-only event store (SQLite, WAL mode)
  fidelity.py        Manifest-receipt integrity verification
  parser.py          Chat export ingestion (JSONL + markdown)
  consolidation.py   Knowledge store + briefing system
  expert_lenses.py   Reasoning frameworks + expert routing
  cli.py             Command-line interface (click)
```

**Design rules:**
1. No theater — every line does something real and verifiable
2. Append-only truth — the ledger never lies
3. AI thinks, code scaffolds — frameworks for reasoning, not fake reasoning
4. One piece at a time — build small, test it works, then build next

## Development

```bash
pytest tests/ -v
ruff check src/ tests/
ruff format src/ tests/
mypy src/divineos/
```

## License

AGPL-3.0-or-later
