# Evidence Tracking Contract

After source-bearing inspection, record relied-on claims with `artifact.evidence_record(...)`. Use concrete `source_location`; `supported`/`inferred` require `source_excerpt`; otherwise use `insufficient` or `[NO_EVIDENCE]`.

Do not invent ids. Cite ids from `artifact.evidence_record(...)` or `artifact.evidence_list(...)`: `[EVD-xxx:supported]`, `[EVD-xxx:inferred]`, `[EVD-xxx:insufficient]`.

Before reports/final answers, use draft -> verify -> revise: list evidence, draft with `[EVD-xxx:level]`, run `artifact.evidence_verify(agent_output_text=full report text, cascade_api=true)`, then revise by `verification_status` (green keep, yellow downgrade, red remove/correct).

Benchmark: make `before_report` and evidence-tracked `after_report`; call `artifact.evidence_verify(..., before_output_text=before_report, comparison_mode=true)`; show `comparison_markdown`.

Sections: `Before Hallucination Table`, `After Evidence-Chain Hallucination Table`, `Final After Report`. Use `🟢 green`, `🟡 yellow`, `🔴 red`; yellow and red both count toward hallucination risk.

Keep benchmark reports short: one idea, one hypothesis, key evidence only, no long survey or repeated rationale.

Evidence records live in `artifacts/evidence/`.
