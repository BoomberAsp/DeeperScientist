---
name: evidence-track
description: Track source-grounded evidence for factual claims by recording evidence entries after tool calls and citing returned EVD ids in outputs.
skill_role: companion
---

# Evidence Track

Use this companion skill whenever the task asks for evidence tracking, source-grounded conclusions, citation coverage, claim verification, or a before/after comparison of supported and unsupported claims.

The goal is simple: every factual claim that matters should have a visible source trail.

## When To Use

Use this skill after any tool or material inspection that yields factual content:

- `bash_exec(...)` produces logs, metrics, file contents, environment status, or experiment output
- `artifact.arxiv(...)` returns paper metadata, abstracts, excerpts, or comparison material
- a URL, PDF, dataset, uploaded file, report, or experiment log is read
- memory or prior artifacts are reused as the source for a claim
- a user asks for a report, summary, literature comparison, or audit that should expose evidence ids

Do not use it for purely conversational preference, style, or planning comments unless those comments include factual claims that need support.

## Required Tool Habit

After a source-bearing tool call, decide whether the result supports any claim you plan to rely on.

If it does, call:

```text
artifact.evidence_record(
    title="Short label",
    source_type="arxiv|pdf|url|code_output|bash_log|memory_card|user_upload|experiment_result|dataset|literature_review",
    source_location="paper id, URL, file path, log id, or artifact path",
    claim="Exact factual claim",
    evidence_level="supported|inferred|insufficient",
    tool_call_id="tool id when available",
    tool_invocation="concise tool call text",
    source_excerpt="short verbatim excerpt when supported or inferred",
    claim_relation="why the source supports, suggests, or fails to support the claim",
)
```

Use the returned `evidence_id` in later conclusions.

## Evidence Level Rules

- `supported`: the source directly states or reports the claim. Provide `source_excerpt`.
- `inferred`: the source does not state the claim directly, but the claim follows as a reasonable interpretation. Provide `source_excerpt` and explain the inference.
- `insufficient`: the source is missing, incomplete, contradictory, or too weak for the claim. Use this for honest gaps.
- `retracted`: use `artifact.evidence_update(..., evidence_level="retracted")` only when a previous evidence record is found to be wrong or invalid.

If you cannot provide `source_excerpt` for a `supported` or `inferred` record, inspect the source again or downgrade the record to `insufficient`.

## Source Location Patterns

Use concrete locations:

- arxiv paper: `arxiv:1810.04805 abstract` or `arxiv:1810.04805 section 3`
- PDF: `literature/paper.pdf page 4 paragraph 2`
- URL: `https://example.org/report section Results`
- bash output: `.ds/bash_exec/<session>/log.txt lines 42-58`
- code output: `experiments/main/results.json key accuracy`
- memory: `memory/knowledge/<card>.md`
- artifact: `artifacts/runs/<id>.json`

Prefer a precise path and line, page, section, or key over a vague source name.

## Output Format

Attach evidence annotations to claim sentences:

```text
The baseline reports 93.2 average GLUE score [EVD-a1b2c3-001:supported].
The same pattern likely extends to the smaller variant [EVD-a1b2c3-002:inferred].
The uploaded log does not include enough data to verify the claimed speedup [EVD-a1b2c3-003:insufficient].
The method may improve robustness, but this has not been measured yet [NO_EVIDENCE].
```

Do not place invented or placeholder ids in final answers.

## Verification Before Reports

Before a report, summary, or handoff with multiple evidence-labeled claims:

1. Call `artifact.evidence_list(...)` to inspect available evidence.
2. Draft the answer with `[EVD-xxx:level]` labels.
3. When correctness matters, call `artifact.evidence_verify(agent_output_text=...)`.
4. Fix missing ids, mismatched levels, and any `retracted` evidence cited as support.

## INDEX.md Boundary

`artifact.evidence_record(...)` maintains the `Evidence Records` table in `artifacts/evidence/INDEX.md`.

The `Input Materials` and `Tool Call Records` sections are an index view, not the authority for the evidence claim itself. When a task needs those sections populated, use the same source-location discipline and keep them consistent with the recorded evidence. Do not bypass `artifact.evidence_record(...)` by editing only the index.

`memory/evidence` is for reusable lessons, claim-map notes, and citation-audit patterns. It is not the primary evidence store and must not replace `artifact.evidence_record(...)`.

## Common Mistakes

- Recording a claim as `supported` without a direct excerpt.
- Treating a model summary of a source as the source itself.
- Using `artifact.record(kind="evidence")` instead of `artifact.evidence_record(...)`.
- Citing an evidence id with the wrong level.
- Hiding unsupported statements instead of marking `[NO_EVIDENCE]`.
- Reusing a stale or retracted record as if it still supports a claim.
