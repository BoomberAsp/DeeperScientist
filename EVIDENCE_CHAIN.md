# DeepScientist Evidence Chain Tracking

**Make LLM-generated research claims auditable, verifiable, and trustworthy.**

When an autonomous research agent writes "The model reaches 93.2% accuracy," where does that number come from? Can you trust it? This module answers those questions by enforcing a structured evidence chain — every factual claim must cite a verifiable source with a verbatim excerpt, and an independent verification layer checks whether the excerpt actually supports the claim.

---

## Table of Contents

1. [Problem](#1-problem)
2. [Architecture Overview](#2-architecture-overview)
3. [Evidence Lifecycle](#3-evidence-lifecycle)
4. [Three-Layer Verification](#4-three-layer-verification)
5. [Storage Architecture](#5-storage-architecture)
6. [New MCP Tools](#6-new-mcp-tools)
7. [Quick Start](#7-quick-start)
8. [Usage Guide](#8-usage-guide)
9. [Output Files & Metrics](#9-output-files--metrics)
10. [Test Cases](#10-test-cases)
11. [Limitations](#11-limitations)
12. [File Map](#12-file-map)
13. [Relationship to DeepScientist](#13-relationship-to-deepscientist)

---

## 1. Problem

LLM research agents face three risks that citations alone cannot solve:

| Risk | Example | This Module's Response |
|------|---------|----------------------|
| **Hallucinated citations** | Agent claims support from a paper that doesn't exist | Layer 1 verifies every `[EVD-xxx]` against the evidence index |
| **Overclaimed support** | Source says "60-93% accuracy," Agent writes "93% accuracy" | Layer 1.5 independently fetches the source and checks excerpt fidelity |
| **Semantic mismatch** | Source is related but doesn't actually support the specific claim | Layer 2 runs external NLI on (claim, source_excerpt) |

The core insight: **having a citation is not the same as having support.** This module makes the distinction explicit and machine-checkable.

---

## 2. Architecture Overview

```
                        ┌──────────────────────────┐
                        │     Agent reads source    │
                        │  (paper / URL / log / ...) │
                        └────────────┬─────────────┘
                                     │
                                     ▼
                        ┌──────────────────────────┐
                        │  artifact.evidence_record │
                        │  claim + source_excerpt    │
                        │  + evidence_level          │
                        └────────────┬─────────────┘
                                     │
                                     ▼
                        ┌──────────────────────────┐
                        │    EVD-*.md  (YAML+MD)    │
                        │    INDEX.md  (fcntl-lock) │
                        └────────────┬─────────────┘
                                     │
                                     ▼
              ┌─────────────────────────────────────────┐
              │        Three-Layer Verification         │
              │                                         │
              │  Layer 1:   Citation Integrity           │
              │     ↳ ID exists? Level matches?          │
              │     ↳ Retracted evidence cited?          │
              │                          ↓               │
              │  Source Fidelity: excerpt in source?     │
              │     ↳ Independent fetch of original      │
              │     ↳ Sliding-window fuzzy match         │
              │                          ↓               │
              │  Layer 2:   Semantic Verification        │
              │     ↳ heuristic → DeBERTa → LLM API      │
              │     ↳ entailment / neutral / contradiction│
              └──────────────────┬──────────────────────┘
                                 │
                                 ▼
              ┌─────────────────────────────────────────┐
              │            Publishable Report            │
              │  green → keep    yellow → downgrade      │
              │  red → remove    [NO_EVIDENCE] → honest  │
              └─────────────────────────────────────────┘
```

Key design principle: **the system does not trust the Agent to judge its own reliability.** Every claim is verified by an independent external pipeline.

---

## 3. Evidence Lifecycle

### 3.1 Evidence Levels

| Level | Meaning | source_excerpt required? | Publishable? |
|-------|---------|------------------------|--------------|
| `supported` | Source directly proves the claim | **Yes (mandatory)** | Yes, if Layer 2 passes |
| `inferred` | Reasonable extrapolation from source | **Yes (mandatory)** | Yes, with caveat |
| `insufficient` | Source is related but too weak | No | No — becomes `[NO_EVIDENCE]` |
| `retracted` | Previously recorded, now invalidated | No | No — treated as error if cited |

### 3.2 Mandatory source_excerpt Constraint

For `supported` and `inferred` records, `source_excerpt` is **not optional**. The schema validator rejects writes without it:

```python
if evidence_level in {"supported", "inferred"} and not source_excerpt:
    errors.append(
        "Evidence level '...' requires source_excerpt "
        "(verbatim quote from the source). This is mandatory for "
        "fact-checking — without it, the claim cannot be independently verified."
    )
```

Without a verbatim excerpt, Layer 2 NLI has no premise to judge against, and the Source Fidelity check has nothing to match. **No excerpt = no verifiability.**

### 3.3 Citation Annotation

Agent output uses inline citation tags:

```
The model reaches 93.2% accuracy [EVD-0c8841da-001:supported].
The model is faster than the baseline [NO_EVIDENCE].
```

- `[EVD-xxx:supported]` — claim backed by evidence record xxx
- `[NO_EVIDENCE]` — claim has no supporting evidence (honest, not hidden)

---

## 4. Three-Layer Verification

### Layer 1: Citation Integrity (Deterministic, 100% Accuracy)

Checks every `[EVD-xxx:level]` reference in the report against `INDEX.md`:

- **Missing**: referenced EVD-id not found in index → **fabricated citation**
- **Mismatched**: `[EVD-xxx:supported]` but evidence is actually `inferred` → **level inflation**
- **Retracted**: cites an evidence record already marked `retracted` → **stale reference**

Layer 1 is deterministic — no model, no probability, no false positives.

### Source Fidelity: Excerpt-In-Source Check (New)

Before trusting the Agent-provided `source_excerpt`, the system independently fetches the original source:

| source_type | Fetch method | Cached? |
|-------------|-------------|---------|
| `arxiv` | `read_arxiv_content()` (abstract + metadata) | Yes, under `artifacts/evidence/sources/` |
| `url` | HTTP GET + HTML-to-text extraction | Yes |
| `code_output`, `bash_log`, etc. | Local file read | No |

A sliding-window fuzzy matcher (SequenceMatcher, threshold 0.85) checks whether the excerpt genuinely appears in the source. Results: `verified` / `partial_match` / `not_found` / `source_unavailable`.

This step addresses a critical trust gap: **the Agent could fabricate or distort a quote**, and NLI would never catch it because NLI only sees the excerpt, not the original.

### Layer 2: Semantic Verification (Pluggable Backends)

Checks whether `source_excerpt` semantically supports `claim`:

```
Backend cascade (recommended):
  heuristic ──→ transformers ──→ LLM API (final)
  (zero-cost    (DeBERTa-v3      (GPT-4 class,
   token         local NLI)       open-domain
   overlap)                       reasoning)
```

| Backend | Cost | Latency | Open-Domain Generalization | When to Use |
|---------|------|---------|---------------------------|-------------|
| `heuristic` | Free | <1ms | Low (token overlap only) | Fast triage / offline |
| `transformers` | Local GPU/CPU | ~100ms | Medium (MNLI/FEVER trained) | Cost-sensitive, closed-domain |
| `api` | API $ | ~1-2s | **High** (GPT-4 zero-shot) | **Recommended for research** |
| `cascade` | Varies | Varies | High (falls back through stages) | **Default — best balance** |

**Important**: For open-domain scientific research, the `api` backend (GPT-4 class) is recommended. DeBERTa-v3's MNLI/FEVER training limits its generalization on highly specialized claims. This aligns with the principle of "using the Agent's own reasoning capability" for verification — when the Agent is backed by a strong LLM, the `api` backend leverages that same class of model for independent judgment.

NLI labels: `entailment` (supports) / `neutral` (related but insufficient) / `contradiction` (conflicts).

### Publishing Rules

| Verification Status | Action | In Publishable Report |
|--------------------|--------|----------------------|
| green | Keep as supported citation | `[EVD-xxx:supported]` |
| yellow | Downgrade or split claim | `[EVD-xxx:inferred]` or `[NO_EVIDENCE]` |
| red | Remove or replace evidence | `[NO_EVIDENCE]` |

---

## 5. Storage Architecture

```
quest_root/
└── artifacts/
    └── evidence/
        ├── INDEX.md              ← unified index, fcntl.LOCK_EX
        ├── EVD-0c8841da-001.md   ← YAML frontmatter + Markdown body
        ├── EVD-a1b2c3d4-002.md
        ├── ...
        ├── sources/              ← cached source content (sha256-keyed)
        │   └── <hash>.json
        └── verification/         ← generated verification reports
            └── evidence_verify-*.md
```

### EVD File Format

```yaml
---
evidence_id: EVD-0c8841da-001
title: Demo accuracy evidence
claim: The model reaches 93.2% accuracy.
evidence_level: supported
source_type: experiment_result
source_location: result.json
source_excerpt: "The model reaches 93.2% accuracy."
source_content_hash: sha256:...
timestamp: 2026-06-02T11:30:48+00:00
---

## Source Excerpt
> The model reaches 93.2% accuracy.

## Relationship to Claim
The excerpt directly states the same accuracy claim.
```

### Design Principles

| Principle | Implementation |
|-----------|---------------|
| **File-Native** | No database, no schema migration. YAML frontmatter = structured metadata. |
| **Lock-Safe** | `fcntl.LOCK_EX` on INDEX.md for atomic read-upsert-write. |
| **Git-Versioned** | Every evidence change is tracked. `git log artifacts/evidence/` = audit trail. |
| **INDEX.md Fast Path** | `evidence_list()` parses one table, not a directory scan. |

---

## 6. New MCP Tools

All tools live under the `artifact` namespace (no new public MCP namespace):

| Tool | When to Call | Key Input | Output |
|------|-------------|-----------|--------|
| `artifact.evidence_record(...)` | After reading source material | `claim`, `source_type`, `source_location`, `source_excerpt`, `evidence_level` | EVD-id for citation |
| `artifact.evidence_list(...)` | Before writing final report | Optional filters (level, source_type) | Structured list of available evidence |
| `artifact.evidence_get(...)` | Need details of a specific record | evidence_id | Full YAML+MD record |
| `artifact.evidence_update(...)` | Evidence needs correction or retraction | evidence_id + changed fields | Updated record |
| `artifact.evidence_verify(...)` | **Before publishing** any report | `agent_output_text`, `verification_mode` | Layer 1 + Source Fidelity + Layer 2 report |
| `artifact.evidence_index_snapshot(...)` | Debugging, external scripting | None | Structured INDEX.md contents |

### Calling Order

1. **After reading**: `evidence_record()` — capture claims with excerpts
2. **Before writing**: `evidence_list()` / `evidence_get()` — know what evidence is available
3. **Before publishing**: `evidence_verify()` — three-layer check
4. **On error**: `evidence_update()` — fix or retract

---

## 7. Quick Start

### Prerequisites

```bash
# Core dependencies (already in DeepScientist)
pip install pyyaml httpx

# Optional: local NLI model
pip install torch transformers sentencepiece

# Optional: ModelScope fallback (if HuggingFace times out)
pip install modelscope

# Optional: API backend
# Set credentials in .env (see below)
```

### .env Configuration (for API backend)

```bash
# OpenAI
NLI_API_KEY=sk-...
NLI_API_BASE_URL=https://api.openai.com/v1
NLI_API_MODEL=gpt-4o-mini

# Or DeepSeek / Kimi / OpenRouter — any OpenAI-compatible endpoint works
```

### 5-Minute Demo

```bash
# 1. Generate evidence table
PYTHONPATH=src python -m deepscientist.artifact.evidence_table \
  --quest-root ~/DeepScientist/quests/evidence-demo \
  --md-out outputs/evidence_table.md \
  --json-out outputs/evidence_table.json

# 2. Run three-layer verification (cascade, no API key needed)
PYTHONPATH=src:scripts python scripts/verify_evidence.py \
  --quest-root ~/DeepScientist/quests/evidence-demo \
  --report outputs/report.md \
  --nli-backend cascade \
  --json-out outputs/verify.json \
  --md-out outputs/verify.md

# 3. Before/after comparison
PYTHONPATH=src:scripts python scripts/before_after_compare.py \
  --before-report outputs/before.md \
  --after-report outputs/after.md \
  --json-out outputs/compare.json \
  --md-out outputs/compare.md

# 4. Run with LLM API final review
PYTHONPATH=src:scripts python scripts/verify_evidence.py \
  --quest-root ~/DeepScientist/quests/evidence-demo \
  --report outputs/report.md \
  --nli-backend cascade \
  --cascade-api \
  --env-file .env \
  --json-out outputs/verify.json
```

---

## 8. Usage Guide

### 8.1 Verification Backends

```bash
# Fastest: heuristic only (token overlap, no model)
--nli-backend heuristic

# Local NLI model (DeBERTa-v3)
--nli-backend transformers

# LLM API only (GPT-4 class, recommended for research)
--nli-backend api --env-file .env

# Cascade (default): heuristic → transformers → optional LLM API
--nli-backend cascade
--nli-backend cascade --cascade-api    # with LLM API final review
```

### 8.2 Model Loading Options

```bash
# Default: ModelScope (works in China without proxy)
--model-source modelscope

# HuggingFace
--model-source huggingface

# Custom model
--model "MoritzLaurer/deberta-v3-base-mnli-fever-anli"
```

### 8.3 Source Fidelity Check

```bash
# Enabled by default — independently fetches source content
# Skip for offline mode:
--skip-source-fetch
```

### 8.4 Generating Reports

```bash
# Annotated report (original + verification appendix)
--annotated-out outputs/annotated.md

# Publishable report (yellow/red citations → [NO_EVIDENCE])
--publishable-out outputs/publishable.md

# Full comparison (before/after + annotated)
--before-text "$(cat outputs/before.md)" --comparison-out outputs/comparison.md
```

---

## 9. Output Files & Metrics

### Generated Output Files

| File | Generated By | Content |
|------|-------------|---------|
| `evidence_table.md` / `.json` | `evidence_table.py` | Structured evidence record table |
| `verify.md` / `.json` | `verify_evidence.py` | Three-layer verification report |
| `compare.md` / `.json` | `before_after_compare.py` | Before/after coverage comparison |
| `annotated_report.md` | `verify_evidence.py` | Original report + detection appendix |
| `publishable_report.md` | `verify_evidence.py` | Cleaned report (unsupported → `[NO_EVIDENCE]`) |

### Key Metrics

| Metric | Meaning |
|--------|---------|
| `citation_completeness` | Fraction of claim sentences with `[EVD-xxx]` or `[NO_EVIDENCE]` annotation |
| `hallucination_rate` | Agent labels `supported` but NLI returns `neutral` / `contradiction` |
| `agent_nli_agreement_rate` | Agent self-label agrees with external NLI verdict |
| `green_supported_rate` | Fraction of claims verified green (safe to publish as-is) |
| `final_hallucination_rate` | Overall risk after accounting for all layers |

### NLI Labels

| Label | Meaning |
|-------|---------|
| `entailment` | source_excerpt supports the claim |
| `neutral` | excerpt is related but doesn't directly support |
| `contradiction` | excerpt conflicts with the claim |
| `unverifiable` | no excerpt available or evidence is insufficient/retracted |
| `skipped` | backend unavailable or disabled |

### Source Fidelity Labels

| Label | Meaning |
|-------|---------|
| `verified` | excerpt found in independently-fetched source |
| `partial_match` | high similarity but not exact |
| `not_found` | excerpt not in source — possible fabrication |
| `source_unavailable` | could not fetch source content |
| `skipped` | check disabled or evidence is insufficient/retracted |

---

## 10. Test Cases

Five end-to-end cases demonstrate the module's behavior:

| Case | Scenario | Key Result |
|------|----------|-----------|
| C1 | Before/after comparison | citation_completeness: 0% → 50%+; unsupported claims now visible as `[NO_EVIDENCE]` |
| C2 | Chinese paper-to-idea | Cross-language NLI produces more yellow/neutral; exposes language mismatch |
| C3 | AlphaFold/RFdiffusion idea | 7 citations: 2 green / 5 yellow; publishable report keeps only green |
| C4 | QQ-initiated idea | 6 refs pass Layer 1; 1 green / 3 yellow / 2 red; red → `replace_evidence` |
| C5 | Atomic vs. compound claims | Short atomic facts: 4/4 green. Long compound claims: 0/6 green — **write shorter claims** |

Key insight from test cases: **atomic, single-fact claims verify much better than compound sentences.** "The model reaches 93.2% accuracy" is verifiable; "The model achieves SOTA on accuracy, speed, and robustness" is not.

---

## 11. Limitations

1. **NLI model generalization.** DeBERTa-v3 (MNLI/FEVER) has limited open-domain scientific generalization. For research use, prefer `--nli-backend api` or `--cascade-api`.

2. **Cross-language claims.** Chinese claims with English excerpts frequently yield neutral/yellow due to translation gaps in the NLI model.

3. **Compound claims.** A single sentence containing multiple facts is hard to verify atomically. Future work: claim splitter.

4. **Source fetch coverage.** Currently supports arXiv (abstract only), URLs, and local files. Full-text PDF extraction and paywalled paper access are future work.

5. **Citation completeness is heuristic.** Based on sentence splitting + length heuristics, not a formal claim parser.

6. **Agent-provided excerpts.** The Source Fidelity check catches fabricated excerpts when the source is fetchable, but for non-fetchable sources (`tool_call`, `memory_card`, `user_upload`), the system still trusts the Agent's excerpt. Future work: extend fetch coverage.

---

## 12. File Map

```
src/deepscientist/artifact/
├── schemas.py              ← evidence payload validation, mandatory source_excerpt
├── service.py              ← CRUD: record, list, get, update, index snapshot
├── evidence_table.py       ← Markdown/JSON evidence table rendering
├── evidence_verifier.py    ← Layer 1 + Source Fidelity + Layer 2 + metrics + reports
├── source_fetcher.py       ← Independent source content fetch + excerpt fidelity check
└── arxiv.py                ← arXiv metadata/content fetch (existing, reused)

src/deepscientist/mcp/
└── server.py               ← 6 MCP tool registrations (evidence_*)

src/skills/evidence-track/
└── SKILL.md                ← Agent skill: when to record, cite, verify

src/prompts/contracts/
└── evidence_tracking.md    ← Injected into every Agent prompt

scripts/
├── verify_evidence.py          ← CLI entry point for three-layer verification
└── before_after_compare.py     ← Before/after citation coverage comparison

outputs/                        ← Demo outputs (poster-ready)
├── schema_card.svg             ← Evidence record schema visualization
├── storage_architecture.svg    ← Storage architecture diagram
├── contribution3_nli_cascade.svg ← NLI cascade verification flow
└── *.json, *.md                ← Demo verification results

tests/
├── test_evidence_tracking.py       ← Backend CRUD tests (20 tests)
├── test_evidence_validation_c.py   ← Verifier + table + compare tests (18 tests)
└── evidence_chain_test/            ← Integration test harness
    ├── test_evidence.md
    └── run_comparison.py
```

---

## 13. Relationship to DeepScientist

This module is an **incremental contribution** to DeepScientist, not a fork:

- **No new public MCP namespace.** All 6 evidence tools live under `artifact.*`.
- **No modification to existing tools.** Quest management, chat, file I/O, and bash_exec remain unchanged.
- **Zero new database dependencies.** Evidence is stored as YAML+Markdown files, versioned by Git.
- **The module is additive**: it adds the ability to record, cite, verify, and audit claims. Existing workflows continue to work unchanged.

The design follows DeepScientist's core principles: file-native, Git-backed, and MCP-compatible.
