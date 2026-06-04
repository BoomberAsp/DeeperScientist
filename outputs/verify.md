# Evidence Verification Report

Quest root: `/home/jackpot/DeepScientist/quests/evidence-demo`
Evidence records: 1

## Metrics

| Metric | Value |
|---|---:|
| agent_nli_agreement_rate | 100.00% |
| hallucination_rate | 0.00% |
| unverifiable_rate | 0.00% |
| citation_completeness | 100.00% |

## Layer 1 Citation Check

- Total references: 1
- Verified: 1
- Mismatched: 0
- Missing: 0
- Retracted but cited: 0

## Layer 2 NLI Check

| Evidence ID | Agent Label | NLI Label | Score | Backend | Rationale |
|---|---|---|---:|---|---|
| EVD-0c8841da-001 | supported | entailment | 1.000 | cascade | heuristic=entailment (1.000); nli=entailment (0.990); llm_api=entailment (1.000). Final label selected from api. |
