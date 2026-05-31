# Evidence Tracking Contract

After a source-bearing tool call or material inspection (`bash_exec(...)`, `artifact.arxiv(...)`, URL/PDF/upload/dataset/log/artifact/memory reads, experiment output), record any factual claim you will rely on with `artifact.evidence_record(...)` before presenting it as established.

Use concrete `source_location` values. `supported` and `inferred` records require `source_excerpt`; if no excerpt or source is available, use `insufficient` or mark the claim `[NO_EVIDENCE]`. Use `retracted` only through `artifact.evidence_update(...)` when an earlier record is invalidated.

Do not invent evidence ids. Cite only ids returned by `artifact.evidence_record(...)` or found through `artifact.evidence_list(...)`: `[EVD-xxx:supported]`, `[EVD-xxx:inferred]`, or `[EVD-xxx:insufficient]`.

Before evidence-labeled reports or handoffs, call `artifact.evidence_list(...)`; when correctness matters, run `artifact.evidence_verify(agent_output_text=...)` and fix missing ids, level mismatches, or retracted citations.

Evidence records live in `artifacts/evidence/`. Use `memory/evidence` only for reusable evidence-tracking lessons, claim maps, and citation-audit patterns.
