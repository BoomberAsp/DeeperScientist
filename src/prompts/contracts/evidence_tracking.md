# Evidence Tracking Contract

After a source-bearing tool call or material inspection (`bash_exec(...)`, `artifact.arxiv(...)`, URL/PDF/upload/dataset/log/artifact/memory reads, experiment output), record any factual claim you will rely on with `artifact.evidence_record(...)` before presenting it as established.

Use concrete `source_location` values. `supported` and `inferred` records require `source_excerpt`; if no excerpt or source is available, use `insufficient` or mark the claim `[NO_EVIDENCE]`. Use `retracted` only through `artifact.evidence_update(...)` when an earlier record is invalidated.

Do not invent evidence ids. Cite only ids returned by `artifact.evidence_record(...)` or found through `artifact.evidence_list(...)`: `[EVD-xxx:supported]`, `[EVD-xxx:inferred]`, or `[EVD-xxx:insufficient]`.

Before evidence-labeled reports, handoffs, paper-facing sections, or final answers, call `artifact.evidence_list(...)`, then run `artifact.evidence_verify(agent_output_text=...)`. Treat its returned `summary`, `layer1`, `layer2`, and `guidance` as blocking review: fix missing ids, level mismatches, retracted citations, and semantic risk items (`neutral`, `contradiction`, `unverifiable` for supported claims) before publishing. Include or summarize `user_visible_markdown` so the verification result is visible to the user.

Evidence records live in `artifacts/evidence/`. Use `memory/evidence` only for reusable evidence-tracking lessons, claim maps, and citation-audit patterns.
