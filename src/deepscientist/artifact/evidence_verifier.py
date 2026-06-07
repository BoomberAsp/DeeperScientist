from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import re
from pathlib import Path
from typing import Any


from .evidence_table import (
    EvidenceRecord,
    evidence_records_to_json,
    load_evidence_records,
    render_evidence_table_markdown,
)


EVIDENCE_REF_RE = re.compile(r"\[(EVD-[^\]:\s]+)(?::([a-zA-Z_-]+))?\]")
NO_EVIDENCE_RE = re.compile(r"\[NO_EVIDENCE\]")
NEGATION_WORDS = {"no", "not", "never", "none", "without", "不", "没有", "未", "无"}
STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "by",
    "is", "are", "was", "were", "be", "been", "that", "this", "it", "as", "at",
    "from", "can", "could", "may", "might", "should", "will", "would",
}


@dataclass(frozen=True)
class NliResult:
    evidence_id: str
    agent_label: str
    nli_label: str
    score: float
    backend: str
    rationale: str
    stages: dict[str, Any] | None = None
    occurrence_id: str = ""
    claimed_level: str | None = None
    report_claim: str = ""
    recorded_claim: str = ""
    section_title: str = ""
    line_number: int = 0
    verification_status: str = "yellow"
    recommended_action: str = "downgrade"
    risk_reason: str = ""
    before_agent_label: str = ""
    before_agent_confidence: str = ""
    external_label: str = ""
    label_delta: str = ""
    hallucination_effect: str = ""
    final_publish_decision: str = ""


@dataclass(frozen=True)
class ClaimOccurrence:
    occurrence_id: str
    evidence_id: str
    claimed_level: str | None
    report_claim: str
    section_title: str
    line_number: int
    reference_text: str


def strip_code_blocks(text: str) -> str:
    lines: list[str] = []
    in_code = False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            lines.append(raw_line)
    return "\n".join(lines)


def parse_evidence_references(text: str) -> list[dict[str, str | None]]:
    clean = strip_code_blocks(text)
    return [
        {"evidence_id": match.group(1), "claimed_level": match.group(2)}
        for match in EVIDENCE_REF_RE.finditer(clean)
    ]


def parse_claim_occurrences(text: str) -> list[ClaimOccurrence]:
    occurrences: list[ClaimOccurrence] = []
    section_title = ""
    in_code = False
    for line_number, raw_line in enumerate((text or "").splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            section_title = _strip_inline_markdown(heading.group(2))
            continue
        matches = list(EVIDENCE_REF_RE.finditer(raw_line))
        if not matches:
            continue
        if _is_reference_section(section_title):
            continue
        for match in matches:
            evidence_id = match.group(1)
            claimed_level = match.group(2)
            occurrence_index = len(occurrences) + 1
            occurrences.append(
                ClaimOccurrence(
                    occurrence_id=f"CLAIM-{occurrence_index:03d}",
                    evidence_id=evidence_id,
                    claimed_level=claimed_level,
                    report_claim=_claim_text_for_reference(raw_line, match.start(), match.end()),
                    section_title=section_title,
                    line_number=line_number,
                    reference_text=match.group(0),
                )
            )
    return occurrences


def layer1_verify(report_text: str, records: list[EvidenceRecord]) -> dict[str, Any]:
    refs = parse_evidence_references(report_text)
    by_id = {record.evidence_id: record for record in records}
    verified: list[str] = []
    mismatched: list[dict[str, str | None]] = []
    missing: list[str] = []
    retracted_but_cited: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for ref in refs:
        evidence_id = str(ref["evidence_id"])
        claimed_level = ref["claimed_level"]
        seen.add(evidence_id)
        record = by_id.get(evidence_id)
        if record is None:
            missing.append(evidence_id)
            continue
        if record.evidence_level == "retracted":
            retracted_but_cited.append({"evidence_id": evidence_id, "claimed_level": claimed_level})
            continue
        if claimed_level and claimed_level != record.evidence_level:
            mismatched.append(
                {
                    "evidence_id": evidence_id,
                    "claimed_level": claimed_level,
                    "actual_level": record.evidence_level,
                }
            )
            continue
        verified.append(evidence_id)
    return {
        "ok": True,
        "total_references": len(refs),
        "verified": verified,
        "verified_count": len(verified),
        "mismatched": mismatched,
        "mismatched_count": len(mismatched),
        "missing": missing,
        "missing_count": len(missing),
        "retracted_but_cited": retracted_but_cited,
        "retracted_but_cited_count": len(retracted_but_cited),
        "unreferenced": [record.evidence_id for record in records if record.evidence_id not in seen],
    }


def run_nli(
    records: list[EvidenceRecord],
    *,
    backend: str,
    model: str | None = None,
    env_file: Path | None = None,
    cascade_api: bool = True,
    model_source: str = "modelscope",
    modelscope_model: str | None = None,
) -> list[NliResult]:
    if backend == "none":
        return [
            NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="skipped",
                score=0.0,
                backend="none",
                rationale="NLI backend disabled.",
            )
            for record in records
        ]
    if backend == "transformers":
        return _run_transformers_nli(records, model=model, model_source=model_source, modelscope_model=modelscope_model)
    if backend == "api":
        return _run_api_nli(records, model=model, env_file=env_file)
    if backend == "cascade":
        return _run_cascade_nli(records, model=model, env_file=env_file, cascade_api=cascade_api, model_source=model_source, modelscope_model=modelscope_model)
    return [_heuristic_nli(record) for record in records]


def run_occurrence_nli(
    occurrences: list[ClaimOccurrence],
    records: list[EvidenceRecord],
    *,
    backend: str,
    model: str | None = None,
    env_file: Path | None = None,
    cascade_api: bool = True,
    model_source: str = "modelscope",
    modelscope_model: str | None = None,
) -> list[NliResult]:
    by_id = {record.evidence_id: record for record in records}
    queued_occurrences: list[ClaimOccurrence] = []
    queued_records: list[EvidenceRecord] = []
    immediate_results: dict[str, NliResult] = {}

    for occurrence in occurrences:
        record = by_id.get(occurrence.evidence_id)
        if record is None:
            immediate_results[occurrence.occurrence_id] = _decorate_occurrence_result(
                NliResult(
                    evidence_id=occurrence.evidence_id,
                    agent_label="missing",
                    nli_label="unverifiable",
                    score=0.0,
                    backend=backend,
                    rationale="Evidence reference is missing from the quest evidence index.",
                ),
                occurrence=occurrence,
                record=None,
                force_status="red",
                force_action="remove",
                force_reason="Missing or fabricated EVD reference.",
            )
            continue
        queued_occurrences.append(occurrence)
        queued_records.append(
            EvidenceRecord(
                evidence_id=record.evidence_id,
                title=record.title,
                claim=occurrence.report_claim or record.claim,
                evidence_level=record.evidence_level,
                source_type=record.source_type,
                source_location=record.source_location,
                source_excerpt=record.source_excerpt,
                claim_relation=record.claim_relation,
                path=record.path,
                timestamp=record.timestamp,
            )
        )

    queued_results = run_nli(
        queued_records,
        backend=backend,
        model=model,
        env_file=env_file,
        cascade_api=cascade_api,
        model_source=model_source,
        modelscope_model=modelscope_model,
    )
    decorated: list[NliResult] = []
    for occurrence, cloned_record, raw_result in zip(queued_occurrences, queued_records, queued_results):
        decorated.append(
            _decorate_occurrence_result(
                raw_result,
                occurrence=occurrence,
                record=by_id.get(occurrence.evidence_id),
            )
        )

    by_occurrence_id = {item.occurrence_id: item for item in decorated}
    by_occurrence_id.update(immediate_results)
    return [by_occurrence_id[occurrence.occurrence_id] for occurrence in occurrences]


def _run_cascade_nli(
    records: list[EvidenceRecord],
    *,
    model: str | None,
    env_file: Path | None,
    cascade_api: bool,
    model_source: str = "modelscope",
    modelscope_model: str | None = None,
) -> list[NliResult]:
    heuristic_results = [_heuristic_nli(record) for record in records]
    transformer_results = _run_transformers_nli(records, model=model, model_source=model_source, modelscope_model=modelscope_model)
    prior_stage_results = []
    for heuristic, transformer in zip(heuristic_results, transformer_results):
        locked_final = _choose_cascade_final(heuristic=heuristic, transformer=transformer)
        prior_stage_results.append(
            {
                "heuristic": _stage_payload(heuristic),
                "nli": _stage_payload(transformer),
                "final_without_llm_api": _stage_payload(locked_final),
            }
        )
    api_results = _run_api_nli(records, model=model, env_file=env_file, prior_stage_results=prior_stage_results) if cascade_api else [
        NliResult(
            evidence_id=record.evidence_id,
            agent_label=record.evidence_level,
            nli_label="skipped",
            score=0.0,
            backend="api",
            rationale="LLM API final review disabled; pass --cascade-api to enable it.",
        )
        for record in records
    ]

    merged: list[NliResult] = []
    for record, heuristic, transformer, api in zip(records, heuristic_results, transformer_results, api_results):
        final = _choose_cascade_final(heuristic=heuristic, transformer=transformer)
        if api.nli_label != "skipped":
            api = NliResult(
                evidence_id=api.evidence_id,
                agent_label=api.agent_label,
                nli_label=final.nli_label,
                score=final.score,
                backend=api.backend,
                rationale=api.rationale,
                stages=api.stages,
            )
        stages = {
            "heuristic": _stage_payload(heuristic),
            "nli": _stage_payload(transformer),
            "llm_api": _stage_payload(api),
        }
        merged.append(
            NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label=final.nli_label,
                score=final.score,
                backend="cascade",
                rationale=_cascade_rationale(heuristic=heuristic, transformer=transformer, api=api, final=final),
                stages=stages,
            )
        )
    return merged


def _choose_cascade_final(*, heuristic: NliResult, transformer: NliResult) -> NliResult:
    if transformer.nli_label not in {"skipped"}:
        return transformer
    return heuristic


def _stage_payload(result: NliResult) -> dict[str, Any]:
    return {
        "label": result.nli_label,
        "score": result.score,
        "backend": result.backend,
        "rationale": result.rationale,
    }


def _cascade_rationale(*, heuristic: NliResult, transformer: NliResult, api: NliResult, final: NliResult) -> str:
    parts = [
        f"heuristic={heuristic.nli_label} ({heuristic.score:.3f})",
        f"nli={transformer.nli_label} ({transformer.score:.3f})",
        f"llm_api_rationale={api.nli_label} ({api.score:.3f})",
    ]
    return "; ".join(parts) + f". Final label selected from {final.backend}; LLM API is rationale-only."


def _run_transformers_nli(
    records: list[EvidenceRecord],
    *,
    model: str | None,
    model_source: str = "modelscope",
    modelscope_model: str | None = None,
) -> list[NliResult]:
    try:
        from transformers import pipeline
    except ModuleNotFoundError:
        return _skipped_nli_results(
            records,
            backend="transformers",
            rationale="Python package `transformers` is not installed; rerun with --nli-backend heuristic or install the model dependencies.",
        )

    try:
        resolved_model = _resolve_transformers_model(
            model=model,
            model_source=model_source,
            modelscope_model=modelscope_model,
        )
        classifier = pipeline(
            "text-classification",
            model=resolved_model,
            top_k=None,
        )
    except Exception as exc:
        return _skipped_nli_results(
            records,
            backend="transformers",
            rationale=f"Transformers NLI model could not be loaded from {model_source}: {exc}",
        )
    results: list[NliResult] = []
    for record in records:
        if not record.source_excerpt.strip():
            results.append(_unverifiable(record, "source_excerpt is missing.", backend="transformers"))
            continue
        try:
            raw = classifier({"text": record.source_excerpt, "text_pair": record.claim})
            labels = raw[0] if raw and isinstance(raw[0], list) else raw
            best = max(labels, key=lambda item: float(item.get("score", 0.0)))
            label = _normalize_nli_label(str(best.get("label") or "neutral"))
            results.append(
                NliResult(
                    evidence_id=record.evidence_id,
                    agent_label=record.evidence_level,
                    nli_label=label,
                    score=float(best.get("score", 0.0)),
                    backend="transformers",
                    rationale=f"Best model label: {best.get('label')}",
                )
            )
        except Exception as exc:
            results.append(
                NliResult(
                    evidence_id=record.evidence_id,
                    agent_label=record.evidence_level,
                    nli_label="skipped",
                    score=0.0,
                    backend="transformers",
                    rationale=f"Transformers NLI inference failed: {exc}",
                )
            )
    return results



def _resolve_transformers_model(*, model: str | None, model_source: str, modelscope_model: str | None) -> str:
    if model_source == "modelscope":
        return _download_modelscope_model(model=model, modelscope_model=modelscope_model)
    return model or os.environ.get("NLI_TRANSFORMERS_MODEL") or "MoritzLaurer/deberta-v3-base-mnli-fever-anli"


def _download_modelscope_model(*, model: str | None, modelscope_model: str | None) -> str:
    model_id = (
        modelscope_model
        or os.environ.get("NLI_MODELSCOPE_MODEL")
        or model
        or "cross-encoder/nli-roberta-base"
    )
    # ModelScope references errno.EREMOTEIO on some paths, but that constant is
    # not defined on macOS/Python combinations. Provide the Linux value so the
    # optional local NLI backend can still initialize.
    import errno as _errno

    if not hasattr(_errno, "EREMOTEIO"):
        _errno.EREMOTEIO = 121  # type: ignore[attr-defined]
    try:
        from modelscope import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package `modelscope` is not installed. Install NLI dependencies with "
            "`pip install 'deepscientist[nli]'` or `pip install modelscope transformers torch`."
        ) from exc
    return str(snapshot_download(model_id))


def _skipped_nli_results(records: list[EvidenceRecord], *, backend: str, rationale: str) -> list[NliResult]:
    return [
        NliResult(
            evidence_id=record.evidence_id,
            agent_label=record.evidence_level,
            nli_label="skipped",
            score=0.0,
            backend=backend,
            rationale=rationale,
        )
        for record in records
    ]

def _run_api_nli(
    records: list[EvidenceRecord],
    *,
    model: str | None,
    env_file: Path | None,
    prior_stage_results: list[dict[str, Any]] | None = None,
) -> list[NliResult]:
    config = _load_api_config(env_file=env_file, model=model)
    missing = [key for key in ("NLI_API_KEY", "NLI_API_BASE_URL", "NLI_API_MODEL") if not config.get(key)]
    if config.get("NLI_API_KEY") == "replace_with_your_api_key":
        missing.append("NLI_API_KEY (still set to placeholder)")
    if missing:
        return [
            NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="skipped",
                score=0.0,
                backend="api",
                rationale=f"Missing API configuration: {', '.join(missing)}.",
            )
            for record in records
        ]

    try:
        import httpx
    except ModuleNotFoundError:
        return [
            NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="skipped",
                score=0.0,
                backend="api",
                rationale="Python package `httpx` is not installed.",
            )
            for record in records
        ]

    timeout = float(config.get("NLI_API_TIMEOUT_SECONDS") or "60")
    results: list[NliResult] = []
    with httpx.Client(timeout=timeout) as client:
        for index, record in enumerate(records):
            prior_stages = prior_stage_results[index] if prior_stage_results and index < len(prior_stage_results) else None
            locked_stage = (prior_stages or {}).get("final_without_llm_api") if isinstance(prior_stages, dict) else None
            if record.evidence_level in {"insufficient", "retracted"}:
                results.append(
                    NliResult(
                        evidence_id=record.evidence_id,
                        agent_label=record.evidence_level,
                        nli_label="unverifiable",
                        score=0.0,
                        backend="api",
                        rationale=f"Agent label is {record.evidence_level}; semantic support is not expected.",
                    )
                )
                continue
            if not record.source_excerpt.strip():
                results.append(_unverifiable(record, "source_excerpt is missing.", backend="api"))
                continue
            try:
                api_result = _call_api_nli(client, record, config=config, prior_stages=prior_stages)
                if isinstance(locked_stage, dict) and locked_stage.get("label") not in {None, "skipped"}:
                    api_result = NliResult(
                        evidence_id=api_result.evidence_id,
                        agent_label=api_result.agent_label,
                        nli_label=str(locked_stage.get("label")),
                        score=float(locked_stage.get("score") or 0.0),
                        backend="api",
                        rationale=api_result.rationale,
                        stages=api_result.stages,
                    )
                results.append(api_result)
            except Exception as exc:  # pragma: no cover - covered by live API runs.
                results.append(
                    NliResult(
                        evidence_id=record.evidence_id,
                        agent_label=record.evidence_level,
                        nli_label="skipped",
                        score=0.0,
                        backend="api",
                        rationale=f"API request failed: {exc}",
                    )
                )
    return results


def _call_api_nli(client: Any, record: EvidenceRecord, *, config: dict[str, str], prior_stages: dict[str, Any] | None = None) -> NliResult:
    url = _join_url(config["NLI_API_BASE_URL"], config.get("NLI_API_CHAT_PATH") or "/chat/completions")
    prior_summary = json.dumps(prior_stages or {}, ensure_ascii=False, indent=2)
    payload = {
        "model": config["NLI_API_MODEL"],
        "temperature": float(config.get("NLI_API_TEMPERATURE") or "0"),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the rationale writer for an evidence-chain verifier. Do not change the verifier label. "
                    "The prior verifier results include final_without_llm_api; that label is locked and authoritative. "
                    "Your job is to explain the textual relation between Source excerpt and Claim, not to justify the "
                    "model score. If the locked label is neutral or contradiction, explain exactly why the source cannot "
                    "support the claim: name the missing entity, metric, condition, causal link, scope, numeric value, "
                    "direction of effect, or contradicting phrase. If the locked label is entailment, name the exact source "
                    "text that supports the claim. Do not say merely that the NLI score is high/low or that the model chose "
                    "a label. Return strict JSON only with keys: label, score, rationale. Set label and score to the locked "
                    "final_without_llm_api values."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Claim:\n{record.claim}\n\n"
                    f"Source excerpt:\n{record.source_excerpt}\n\n"
                    f"Prior verifier results:\n{prior_summary}\n\n"
                    "Return a source-grounded rationale for the locked final_without_llm_api label. Focus on why the source excerpt does or does not support the claim."
                ),
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {config['NLI_API_KEY']}",
        "Content-Type": "application/json",
    }
    response = client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    parsed = _parse_api_nli_response(response.json())
    return NliResult(
        evidence_id=record.evidence_id,
        agent_label=record.evidence_level,
        nli_label=parsed["label"],
        score=parsed["score"],
        backend="api",
        rationale=parsed["rationale"],
    )


def _parse_api_nli_response(data: dict[str, Any]) -> dict[str, Any]:
    content = ""
    try:
        content = str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        content = json.dumps(data, ensure_ascii=False)
    content = _strip_json_fence(content.strip())
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {"label": content, "score": 0.0, "rationale": "API response was not valid JSON."}
    label = _normalize_nli_label(str(payload.get("label") or payload.get("nli_label") or "neutral"))
    try:
        score = float(payload.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = min(max(score, 0.0), 1.0)
    rationale = str(payload.get("rationale") or payload.get("reason") or "").strip()
    return {"label": label, "score": score, "rationale": rationale or "No rationale returned by API."}


def _heuristic_nli(record: EvidenceRecord) -> NliResult:
    if record.evidence_level in {"insufficient", "retracted"}:
        return NliResult(
            evidence_id=record.evidence_id,
            agent_label=record.evidence_level,
            nli_label="unverifiable",
            score=0.0,
            backend="heuristic",
            rationale=f"Agent label is {record.evidence_level}; semantic support is not expected.",
        )
    if not record.source_excerpt.strip():
        return _unverifiable(record, "source_excerpt is missing.")

    claim_tokens = _tokens(record.claim)
    excerpt_tokens = _tokens(record.source_excerpt)
    if not claim_tokens:
        return _unverifiable(record, "claim has no comparable tokens.")
    overlap = claim_tokens & excerpt_tokens
    overlap_score = len(overlap) / max(len(claim_tokens), 1)
    claim_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", record.claim))
    excerpt_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", record.source_excerpt))
    numbers_ok = not claim_numbers or claim_numbers <= excerpt_numbers
    negation_conflict = _has_negation(record.claim) != _has_negation(record.source_excerpt)

    if negation_conflict and overlap_score >= 0.35:
        label = "contradiction"
        score = min(0.95, overlap_score + 0.2)
        rationale = "Claim and excerpt share terms but differ in negation."
    elif overlap_score >= 0.62 and numbers_ok:
        label = "entailment"
        score = overlap_score
        rationale = "High token overlap and numeric values are covered by the excerpt."
    elif overlap_score >= 0.35 and numbers_ok:
        label = "neutral"
        score = overlap_score
        rationale = "Partial overlap; excerpt may be related but does not directly entail the claim."
    else:
        label = "neutral"
        score = overlap_score
        rationale = "Low overlap or missing numeric support."

    return NliResult(
        evidence_id=record.evidence_id,
        agent_label=record.evidence_level,
        nli_label=label,
        score=round(float(score), 4),
        backend="heuristic",
        rationale=rationale,
    )


def _decorate_record_result(result: NliResult, record: EvidenceRecord) -> NliResult:
    status, action, reason = _verification_decision(result)
    before_label = result.agent_label or record.evidence_level
    return NliResult(
        evidence_id=result.evidence_id,
        agent_label=result.agent_label,
        nli_label=result.nli_label,
        score=result.score,
        backend=result.backend,
        rationale=result.rationale,
        stages=result.stages,
        occurrence_id="",
        claimed_level=record.evidence_level,
        report_claim=record.claim,
        recorded_claim=record.claim,
        section_title="",
        line_number=0,
        verification_status=status,
        recommended_action=action,
        risk_reason=reason,
        before_agent_label=before_label,
        before_agent_confidence=_before_agent_confidence(before_label),
        external_label=result.nli_label,
        label_delta=_label_delta(before_label, result.nli_label, status),
        hallucination_effect=_hallucination_effect(status, action),
        final_publish_decision=_final_publish_decision(status, action),
    )


def _decorate_occurrence_result(
    result: NliResult,
    *,
    occurrence: ClaimOccurrence,
    record: EvidenceRecord | None,
    force_status: str | None = None,
    force_action: str | None = None,
    force_reason: str | None = None,
) -> NliResult:
    status, action, reason = _verification_decision(result)
    if force_status:
        status = force_status
    if force_action:
        action = force_action
    if force_reason:
        reason = force_reason
    if record is not None and record.evidence_level == "retracted":
        status = "red"
        action = "remove"
        reason = _join_reasons(reason, "Evidence record is retracted.")
    if record is not None and occurrence.claimed_level and occurrence.claimed_level != record.evidence_level:
        mismatch = f"Report claimed level `{occurrence.claimed_level}` but evidence record is `{record.evidence_level}`."
        reason = _join_reasons(reason, mismatch)
        if status != "red":
            status = "yellow"
            action = "rewrite"
    before_label = occurrence.claimed_level or result.agent_label or (record.evidence_level if record else "missing")
    return NliResult(
        evidence_id=result.evidence_id,
        agent_label=result.agent_label,
        nli_label=result.nli_label,
        score=result.score,
        backend=result.backend,
        rationale=result.rationale,
        stages=result.stages,
        occurrence_id=occurrence.occurrence_id,
        claimed_level=occurrence.claimed_level,
        report_claim=occurrence.report_claim,
        recorded_claim=record.claim if record else "",
        section_title=occurrence.section_title,
        line_number=occurrence.line_number,
        verification_status=status,
        recommended_action=action,
        risk_reason=reason,
        before_agent_label=before_label,
        before_agent_confidence=_before_agent_confidence(before_label),
        external_label=result.nli_label,
        label_delta=_label_delta(before_label, result.nli_label, status),
        hallucination_effect=_hallucination_effect(status, action),
        final_publish_decision=_final_publish_decision(status, action),
    )


def _verification_decision(result: NliResult) -> tuple[str, str, str]:
    if result.agent_label == "retracted":
        return "red", "remove", "Evidence record is retracted."
    if result.agent_label == "insufficient":
        return "yellow", "downgrade", "Evidence was recorded as insufficient; do not use it as support."
    if result.nli_label == "contradiction":
        return "red", "replace_evidence", "Source excerpt contradicts the report claim."
    if result.nli_label == "entailment" and result.score >= 0.75:
        return "green", "keep", "Source excerpt directly supports the report claim."
    if result.nli_label == "entailment":
        return "yellow", "downgrade", "Source appears supportive but verifier confidence is below the green threshold."
    if result.nli_label == "neutral":
        return "yellow", "downgrade", "Source is related but does not directly support the report claim."
    if result.nli_label == "unverifiable":
        return "yellow", "rewrite", "Source support could not be independently verified."
    if result.nli_label == "skipped":
        return "yellow", "rewrite", "External semantic verification was skipped."
    return "yellow", "rewrite", "Verifier returned an uncertain label."


def _join_reasons(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _before_agent_confidence(label: str) -> str:
    normalized = str(label or "").strip().lower()
    mapping = {
        "supported": "self_supported",
        "inferred": "self_inferred",
        "insufficient": "self_insufficient",
        "retracted": "self_retracted",
        "missing": "missing_reference",
    }
    return mapping.get(normalized, normalized or "unknown")


def _label_delta(before_label: str, external_label: str, status: str) -> str:
    before = str(before_label or "unknown").strip() or "unknown"
    external = str(external_label or "unknown").strip() or "unknown"
    rendered_status = str(status or "yellow").strip() or "yellow"
    return f"{before} -> {external} / {rendered_status}"


def _hallucination_effect(status: str, action: str) -> str:
    rendered_status = str(status or "").strip().lower()
    rendered_action = str(action or "").strip().lower()
    if rendered_status == "green":
        return "unchanged_supported"
    if rendered_status == "red":
        return "corrected_or_removed_error"
    if rendered_action in {"downgrade", "rewrite"}:
        return "downgraded_to_uncertain"
    return "unresolved_uncertainty"


def _final_publish_decision(status: str, action: str) -> str:
    rendered_status = str(status or "").strip().lower()
    rendered_action = str(action or "").strip().lower()
    if rendered_status == "green":
        return "keep_as_supported"
    if rendered_status == "yellow":
        if rendered_action == "rewrite":
            return "rewrite_or_mark_unresolved"
        return "keep_only_with_cautious_wording"
    if rendered_status == "red":
        if rendered_action == "replace_evidence":
            return "replace_evidence_or_remove_claim"
        return "remove_or_correct_claim"
    return "review_before_publish"


def compute_metrics(report_text: str, records: list[EvidenceRecord], nli_results: list[NliResult]) -> dict[str, Any]:
    status_counts = {
        "green": len([item for item in nli_results if item.verification_status == "green"]),
        "yellow": len([item for item in nli_results if item.verification_status == "yellow"]),
        "red": len([item for item in nli_results if item.verification_status == "red"]),
    }
    comparable = [
        item
        for item in nli_results
        if item.agent_label in {"supported", "inferred"} and item.nli_label not in {"skipped"}
    ]
    agreements = [
        item
        for item in comparable
        if (item.agent_label == "supported" and item.nli_label == "entailment")
        or (item.agent_label == "inferred" and item.nli_label in {"entailment", "neutral"})
    ]
    unverifiable = [item for item in nli_results if item.nli_label in {"unverifiable", "skipped"}]
    claim_total = _estimate_claim_sentence_count(report_text)
    cited_claims = len(parse_evidence_references(report_text))
    no_evidence_claims = len(NO_EVIDENCE_RE.findall(strip_code_blocks(report_text)))
    denominator = max(claim_total, cited_claims + no_evidence_claims, 1)
    verification_total = max(len(nli_results), 1)
    red_error_rate = _ratio(status_counts["red"], verification_total)
    yellow_uncertain_rate = _ratio(status_counts["yellow"], verification_total)
    green_supported_rate = _ratio(status_counts["green"], verification_total)
    strict_hallucination_rate = _ratio(status_counts["yellow"] + status_counts["red"], verification_total)
    return {
        "agent_nli_agreement_rate": _ratio(len(agreements), len(comparable)),
        "hallucination_rate": strict_hallucination_rate,
        "unverifiable_rate": _ratio(len(unverifiable), len(nli_results)),
        "citation_completeness": _ratio(cited_claims, denominator),
        "claim_sentence_estimate": denominator,
        "evidence_citation_count": cited_claims,
        "no_evidence_count": no_evidence_claims,
        "verified_claim_count": len(nli_results),
        "green_supported_count": status_counts["green"],
        "yellow_uncertain_count": status_counts["yellow"],
        "red_error_count": status_counts["red"],
        "green_supported_rate": green_supported_rate,
        "yellow_uncertain_rate": yellow_uncertain_rate,
        "red_error_rate": red_error_rate,
        "semantic_risk_rate": strict_hallucination_rate,
        "unsupported_claim_count": status_counts["yellow"] + status_counts["red"] + no_evidence_claims,
        "downgraded_claim_count": status_counts["yellow"],
        "removed_claim_count": status_counts["red"],
        "final_hallucination_rate": strict_hallucination_rate,
    }


def build_report(
    quest_root: Path,
    report_text: str,
    *,
    backend: str,
    model: str | None = None,
    env_file: Path | None = None,
    cascade_api: bool = True,
    model_source: str = "modelscope",
    modelscope_model: str | None = None,
) -> dict[str, Any]:
    records = load_evidence_records(quest_root)
    layer1 = layer1_verify(report_text, records)
    claim_occurrences = parse_claim_occurrences(report_text)
    if claim_occurrences:
        nli_results = run_occurrence_nli(
            claim_occurrences,
            records,
            backend=backend,
            model=model,
            env_file=env_file,
            cascade_api=cascade_api,
            model_source=model_source,
            modelscope_model=modelscope_model,
        )
        verification_scope = "report_claim_occurrences"
    else:
        raw_results = run_nli(
            records,
            backend=backend,
            model=model,
            env_file=env_file,
            cascade_api=cascade_api,
            model_source=model_source,
            modelscope_model=modelscope_model,
        )
        nli_results = [
            _decorate_record_result(result, record)
            for record, result in zip(records, raw_results)
        ]
        verification_scope = "evidence_records"
    return {
        "ok": True,
        "quest_root": str(quest_root),
        "evidence_total": len(records),
        "layer1": layer1,
        "layer2": {
            "backend": backend,
            "verification_scope": verification_scope,
            "claim_occurrence_count": len(claim_occurrences),
            "claim_occurrences": [asdict(item) for item in claim_occurrences],
            "results": [asdict(item) for item in nli_results],
        },
        "metrics": compute_metrics(report_text, records, nli_results),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    layer1 = payload["layer1"]
    lines = [
        "# Evidence Verification",
        "",
        "| Item | Value |",
        "|---|---:|",
        f"| Evidence records | {payload['evidence_total']} |",
        f"| Cited references | {layer1['total_references']} |",
        f"| Layer 1 verified | {layer1['verified_count']} / {layer1['total_references']} |",
        f"| Missing / mismatched / retracted | {layer1['missing_count']} / {layer1['mismatched_count']} / {layer1['retracted_but_cited_count']} |",
        f"| {_status_display('green')} / {_status_display('yellow')} / {_status_display('red')} | {metrics['green_supported_count']} / {metrics['yellow_uncertain_count']} / {metrics['red_error_count']} |",
        f"| Final hallucination risk | {metrics['final_hallucination_rate']:.2%} |",
        f"| Citation completeness | {metrics['citation_completeness']:.2%} |",
        "",
        "## Hallucination Detection Table",
        "",
    ]
    lines.extend(_render_claim_level_table(payload, compact=True))
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify DeepScientist evidence chains with Layer 1 citation checks and Layer 2 NLI checks.")
    parser.add_argument("--quest-root", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Markdown/text report containing [EVD-xxx:level] references.")
    parser.add_argument("--text", default="", help="Inline report text. Used when --report is omitted.")
    parser.add_argument("--nli-backend", choices=("cascade", "heuristic", "transformers", "api", "none"), default="cascade")
    parser.add_argument("--model", default=None, help="Model override. HuggingFace model for transformers/cascade NLI; API model for api/cascade API.")
    parser.add_argument("--model-source", choices=("huggingface", "modelscope"), default="modelscope", help="Where transformers/cascade should load the NLI model from.")
    parser.add_argument("--modelscope-model", default=None, help="ModelScope model id for --model-source modelscope. Defaults to --model or NLI_MODELSCOPE_MODEL.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Environment file for --nli-backend api or --cascade-api.")
    parser.add_argument("--cascade-api", action="store_true", help="With --nli-backend cascade, call the LLM API to generate rationale from heuristic/NLI results without changing labels.")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--annotated-out", type=Path, help="Write the original report plus an evidence verification appendix.")
    parser.add_argument("--publishable-out", type=Path, help="Write a revised report where yellow/red EVD citations are replaced with [NO_EVIDENCE].")
    parser.add_argument("--before-text", default="", help="Optional before/no-evidence draft used for one-turn benchmark output.")
    parser.add_argument("--comparison-out", type=Path, help="Write before table, after table, and annotated after report together.")
    args = parser.parse_args(argv)

    report_text = args.report.read_text(encoding="utf-8") if args.report else str(args.text or "")
    payload = build_report(
        args.quest_root,
        report_text,
        backend=args.nli_backend,
        model=args.model,
        env_file=args.env_file,
        cascade_api=args.cascade_api,
        model_source=args.model_source,
        modelscope_model=args.modelscope_model,
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(payload), encoding="utf-8")
    if args.annotated_out:
        args.annotated_out.parent.mkdir(parents=True, exist_ok=True)
        args.annotated_out.write_text(render_annotated_report(report_text, payload), encoding="utf-8")
    if args.publishable_out:
        args.publishable_out.parent.mkdir(parents=True, exist_ok=True)
        args.publishable_out.write_text(render_publishable_report(report_text, payload), encoding="utf-8")
    if args.comparison_out:
        args.comparison_out.parent.mkdir(parents=True, exist_ok=True)
        args.comparison_out.write_text(render_comparison_markdown(args.before_text, report_text, payload), encoding="utf-8")
    if not args.json_out and not args.md_out and not args.annotated_out and not args.publishable_out and not args.comparison_out:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _claim_text_for_reference(line: str, start: int, end: int) -> str:
    raw = str(line or "").strip()
    if not raw:
        return ""
    left = -1
    for idx in range(start - 1, -1, -1):
        if _is_sentence_boundary(raw, idx):
            left = idx
            break
    right = len(raw) - 1
    for idx in range(end, len(raw)):
        if _is_sentence_boundary(raw, idx):
            right = idx
            break
    fragment = raw[left + 1:right + 1].strip()
    if not fragment or len(EVIDENCE_REF_RE.sub("", fragment).strip()) < 8:
        fragment = raw
    fragment = EVIDENCE_REF_RE.sub("", fragment)
    fragment = NO_EVIDENCE_RE.sub("", fragment)
    return _strip_inline_markdown(fragment)


def _is_reference_section(section_title: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(section_title or "").strip().lower())
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "source map",
            "references",
            "bibliography",
            "citation index",
            "evidence index",
            "reference list",
            "参考文献",
            "证据索引",
            "来源索引",
        )
    )


def _is_sentence_boundary(text: str, idx: int) -> bool:
    char = text[idx]
    if char not in {".", "!", "?", "。", "！", "？", ";", "；"}:
        return False
    if char == "." and 0 < idx < len(text) - 1 and text[idx - 1].isdigit() and text[idx + 1].isdigit():
        return False
    return True


def _strip_inline_markdown(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([.,!?;:。！？；：])", r"\1", cleaned)
    return cleaned.strip(" -:\t")


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]*|\d+(?:\.\d+)?%?|[\u4e00-\u9fff]", text or "")
        if (len(token) > 1 or re.fullmatch(r"[\u4e00-\u9fff]", token)) and token.lower() not in STOPWORDS
    }


def _has_negation(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in NEGATION_WORDS)


def _normalize_nli_label(label: str) -> str:
    normalized = label.strip().lower()
    if "entail" in normalized or normalized in {"support", "supported", "supports"}:
        return "entailment"
    if "contrad" in normalized or "refute" in normalized:
        return "contradiction"
    return "neutral"


def _load_api_config(*, env_file: Path | None, model: str | None) -> dict[str, str]:
    config: dict[str, str] = {}
    if env_file and env_file.exists():
        config.update(_read_env_file(env_file))
    for key in (
        "NLI_API_KEY",
        "NLI_API_BASE_URL",
        "NLI_API_CHAT_PATH",
        "NLI_API_MODEL",
        "NLI_API_TIMEOUT_SECONDS",
        "NLI_API_TEMPERATURE",
    ):
        value = os.environ.get(key)
        if value is not None:
            config[key] = value
    if model:
        config["NLI_API_MODEL"] = model
    return {key: str(value).strip() for key, value in config.items() if str(value).strip()}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _strip_json_fence(content: str) -> str:
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content.strip()


def _unverifiable(record: EvidenceRecord, rationale: str, *, backend: str = "heuristic") -> NliResult:
    return NliResult(
        evidence_id=record.evidence_id,
        agent_label=record.evidence_level,
        nli_label="unverifiable",
        score=0.0,
        backend=backend,
        rationale=rationale,
    )


def _estimate_claim_sentence_count(text: str) -> int:
    clean = strip_code_blocks(text)
    candidates = []
    for raw in re.split(r"(?<=[.!?。！？])\s+|\n+", clean):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("|") or re.fullmatch(r"[-*]+", line):
            continue
        if len(line) >= 18 or EVIDENCE_REF_RE.search(line) or NO_EVIDENCE_RE.search(line):
            candidates.append(line)
    return len(candidates)


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _md_cell(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip().replace("|", "\\|")
    if len(text) > 160:
        text = text[:157].rstrip() + "..."
    return text or "-"





def verify_evidence_integrated(
    quest_root: Path,
    *,
    agent_output_text: str,
    verification_mode: str = "cascade",
    include_evidence_table: bool = True,
    cascade_api: bool = True,
    model_source: str = "modelscope",
    model: str | None = None,
    modelscope_model: str | None = "cross-encoder/nli-roberta-base",
    env_file: Path | str | None = Path(".env"),
    write_artifacts: bool = True,
    artifact_prefix: str = "evidence_verify",
    before_output_text: str = "",
    comparison_mode: bool = False,
) -> dict[str, Any]:
    """Run the C-part integrated evidence verifier for MCP/tool use."""
    resolved_env_file = Path(env_file) if env_file else None
    payload = build_report(
        quest_root,
        agent_output_text,
        backend=verification_mode,
        model=model,
        env_file=resolved_env_file,
        cascade_api=cascade_api,
        model_source=model_source,
        modelscope_model=modelscope_model,
    )
    records = load_evidence_records(quest_root)
    evidence_table = evidence_records_to_json(records) if include_evidence_table else None
    user_visible_markdown = render_user_visible_markdown(payload)
    verify_markdown = render_markdown(payload)
    annotated_report_markdown = render_annotated_report(agent_output_text, payload)
    publishable_report_markdown = render_publishable_report(agent_output_text, payload)
    before_detection = build_before_detection(before_output_text) if before_output_text.strip() else None
    before_detection_markdown = render_before_detection_markdown(before_detection) if before_detection else ""
    after_detection_markdown = render_after_detection_markdown(payload)
    comparison_markdown = ""
    if comparison_mode or before_detection is not None:
        comparison_markdown = render_comparison_markdown(before_output_text, agent_output_text, payload)
        user_visible_markdown = comparison_markdown
    artifact_paths: dict[str, str] = {}
    if write_artifacts:
        artifact_paths = _write_verification_artifacts(
            quest_root,
            payload=payload,
            verify_markdown=verify_markdown,
            annotated_report_markdown=annotated_report_markdown,
            publishable_report_markdown=publishable_report_markdown,
            comparison_markdown=comparison_markdown or None,
            evidence_table_markdown=render_evidence_table_markdown(records) if include_evidence_table else None,
            artifact_prefix=artifact_prefix,
        )
    layer1 = payload["layer1"]
    metrics = payload["metrics"]
    summary = {
        "evidence_total": payload["evidence_total"],
        "total_references": layer1["total_references"],
        "verified_count": layer1["verified_count"],
        "mismatched_count": layer1["mismatched_count"],
        "missing_count": layer1["missing_count"],
        "retracted_but_cited_count": layer1["retracted_but_cited_count"],
        "hallucination_rate": metrics["hallucination_rate"],
        "green_supported_count": metrics["green_supported_count"],
        "yellow_uncertain_count": metrics["yellow_uncertain_count"],
        "red_error_count": metrics["red_error_count"],
        "final_hallucination_rate": metrics["final_hallucination_rate"],
        "citation_completeness": metrics["citation_completeness"],
        "unverifiable_rate": metrics["unverifiable_rate"],
    }
    result = {
        "ok": True,
        "summary": summary,
        "layer1": layer1,
        "layer2": payload["layer2"],
        "metrics": metrics,
        "evidence_table": evidence_table,
        "user_visible_markdown": user_visible_markdown,
        "before_detection": before_detection,
        "before_detection_markdown": before_detection_markdown,
        "after_detection_markdown": after_detection_markdown,
        "comparison_markdown": comparison_markdown,
        "annotated_report_markdown": annotated_report_markdown,
        "publishable_report_markdown": publishable_report_markdown,
        "artifact_paths": artifact_paths,
        "guidance": _verification_guidance(layer1=layer1, layer2=payload["layer2"], metrics=metrics),
    }
    # Backward-compatible top-level Layer 1 fields used by older tests and agents.
    result.update(layer1)
    result["verification_rate"] = f"{layer1['verified_count'] / layer1['total_references'] * 100:.1f}%" if layer1["total_references"] else "N/A"
    return result


def render_user_visible_markdown(payload: dict[str, Any]) -> str:
    layer1 = payload["layer1"]
    metrics = payload["metrics"]
    risk_items: list[str] = []
    for item in layer1.get("missing", []):
        risk_items.append(f"- Missing evidence reference: `{item}`")
    for item in layer1.get("mismatched", []):
        risk_items.append(
            f"- Level mismatch: `{item.get('evidence_id')}` claimed `{item.get('claimed_level')}`, actual `{item.get('actual_level')}`"
        )
    for item in layer1.get("retracted_but_cited", []):
        risk_items.append(f"- Retracted evidence cited: `{item.get('evidence_id')}`")
    for item in payload["layer2"].get("results", [])[:8]:
        if item.get("verification_status") in {"yellow", "red"}:
            risk_items.append(
                f"- {str(item.get('verification_status')).upper()} `{item.get('occurrence_id') or item.get('evidence_id')}` "
                f"({item.get('evidence_id')}) -> `{item.get('recommended_action')}`: {item.get('risk_reason') or item.get('rationale')}"
            )
    lines = [
        "## Evidence Verification Summary",
        "",
        f"- {_status_display('green')} / {_status_display('yellow')} / {_status_display('red')}: {metrics['green_supported_count']} / {metrics['yellow_uncertain_count']} / {metrics['red_error_count']}",
        f"- Final hallucination risk: {metrics['final_hallucination_rate']:.2%}",
        f"- Layer 1 verified: {layer1['verified_count']} / {layer1['total_references']}",
        f"- Citation completeness: {metrics['citation_completeness']:.2%}",
        "",
        "### Risk Items",
    ]
    lines.extend(risk_items or ["None."])
    lines.extend(["", "### Hallucination Detection Table", ""])
    lines.extend(_render_claim_level_table(payload, compact=True))
    return "\n".join(lines).rstrip() + "\n"


def render_annotated_report(report_text: str, payload: dict[str, Any]) -> str:
    lines = [str(report_text or "").rstrip(), "", "## Hallucination Detection", ""]
    metrics = payload["metrics"]
    lines.extend(
        [
            f"- {_status_display('green')} / {_status_display('yellow')} / {_status_display('red')}: {metrics['green_supported_count']} / {metrics['yellow_uncertain_count']} / {metrics['red_error_count']}",
            f"- Final hallucination risk: {metrics['final_hallucination_rate']:.2%}",
            "",
        ]
    )
    lines.extend(_render_claim_level_table(payload, compact=True))
    return "\n".join(lines).rstrip() + "\n"


def render_publishable_report(report_text: str, payload: dict[str, Any]) -> str:
    """Return a report text that does not cite yellow/red evidence as support."""
    lines = _strip_existing_verification_appendix(str(report_text or "")).rstrip().splitlines()
    unsafe_notes: list[str] = []
    by_line: dict[int, list[dict[str, Any]]] = {}
    for item in payload.get("layer2", {}).get("results", []):
        status = str(item.get("verification_status") or "").strip().lower()
        if status not in {"yellow", "red"}:
            continue
        line_number = int(item.get("line_number") or 0)
        if line_number > 0:
            by_line.setdefault(line_number, []).append(item)

    for line_number, items in by_line.items():
        idx = line_number - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        for item in items:
            reference_text = str(item.get("reference_text") or "")
            if not reference_text:
                reference_text = f"[{item.get('evidence_id', '')}:{item.get('claimed_level') or 'supported'}]"
            if reference_text and reference_text in line:
                line = line.replace(reference_text, "[NO_EVIDENCE]", 1)
            unsafe_notes.append(
                f"- {item.get('occurrence_id') or item.get('evidence_id')}: "
                f"{item.get('evidence_id')} was {item.get('verification_status')} "
                f"({item.get('recommended_action')}); citation replaced with [NO_EVIDENCE]."
            )
        lines[idx] = line

    if unsafe_notes:
        lines.extend(
            [
                "",
                "## Evidence Revision Notes",
                "",
                "The verifier did not accept the following citations as publishable support. "
                "Their inline EVD references were removed from the revised text and replaced with [NO_EVIDENCE].",
                "",
                *unsafe_notes,
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _strip_existing_verification_appendix(report_text: str) -> str:
    lines = str(report_text or "").splitlines()
    for idx, line in enumerate(lines):
        normalized = line.strip().lower()
        if normalized in {"## hallucination detection", "## evidence revision notes"}:
            return "\n".join(lines[:idx]).rstrip()
    return str(report_text or "")


def build_before_detection(before_text: str) -> dict[str, Any]:
    judgments = _self_confidence_judgments(before_text)
    status_counts = _status_counts(judgments)
    total = len(judgments)
    hallucination_rate = _ratio(status_counts["yellow"] + status_counts["red"], total)
    return {
        "ok": True,
        "detection_method": "agent_self_confidence",
        "metrics": {
            "claim_count": total,
            "green_supported_count": status_counts["green"],
            "yellow_uncertain_count": status_counts["yellow"],
            "red_error_count": status_counts["red"],
            "final_hallucination_rate": hallucination_rate,
        },
        "results": judgments,
    }


def render_before_detection_markdown(before_detection: dict[str, Any]) -> str:
    metrics = before_detection.get("metrics", {})
    lines = [
        "## Before Hallucination Table",
        "",
        f"- Detection method: `{before_detection.get('detection_method', 'agent_self_confidence')}`",
        f"- {_status_display('green')} / {_status_display('yellow')} / {_status_display('red')}: {metrics.get('green_supported_count', 0)} / {metrics.get('yellow_uncertain_count', 0)} / {metrics.get('red_error_count', 0)}",
        f"- Hallucination risk: {float(metrics.get('final_hallucination_rate', 0.0) or 0.0):.2%}",
        "",
    ]
    lines.extend(_render_judgment_table(before_detection.get("results", []), compact=True))
    return "\n".join(lines).rstrip() + "\n"


def render_after_detection_markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    lines = [
        "## After Evidence-Chain Hallucination Table",
        "",
        "- Detection method: `external_evidence_chain`",
        f"- {_status_display('green')} / {_status_display('yellow')} / {_status_display('red')}: {metrics['green_supported_count']} / {metrics['yellow_uncertain_count']} / {metrics['red_error_count']}",
        f"- Hallucination risk: {metrics['final_hallucination_rate']:.2%}",
        "",
    ]
    lines.extend(_render_claim_level_table(payload, compact=True))
    return "\n".join(lines).rstrip() + "\n"


def render_comparison_markdown(before_text: str, after_text: str, payload: dict[str, Any]) -> str:
    before_detection = build_before_detection(before_text)
    before_metrics = before_detection["metrics"]
    after_metrics = payload["metrics"]
    before_risk = float(before_metrics.get("final_hallucination_rate", 0.0) or 0.0)
    after_risk = float(after_metrics.get("final_hallucination_rate", 0.0) or 0.0)
    direction = "decreased" if after_risk < before_risk else "did not decrease"
    lines = [
        "# Evidence-Chain Hallucination Benchmark",
        "",
        f"Hallucination risk {direction}: {before_risk:.2%} -> {after_risk:.2%}.",
        "",
        render_before_detection_markdown(before_detection).rstrip(),
        "",
        render_after_detection_markdown(payload).rstrip(),
        "",
        "## Final After Report",
        "",
        render_publishable_report(after_text, payload).rstrip(),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _render_claim_level_table(payload: dict[str, Any], *, compact: bool) -> list[str]:
    if compact:
        header = "| Claim | Evidence ID | Before Label | After Label | Status | Action |"
        separator = "|---|---|---|---|---|---|"
    else:
        header = "| Claim | Evidence ID | Before Label | After Label | Score | Status | Action |"
        separator = "|---|---|---|---|---:|---|---|"
    lines = [header, separator]
    results = payload["layer2"].get("results", [])
    visible_results = results[:8] if compact else results
    for item in visible_results:
        before_label = item.get("before_agent_label") or item.get("agent_label", "")
        external_label = item.get("external_label") or item.get("nli_label", "")
        status = item.get("verification_status", "yellow")
        action = item.get("recommended_action", "")
        if compact:
            values = (
                item.get("report_claim") or item.get("recorded_claim") or "",
                item.get("evidence_id", ""),
                before_label,
                external_label,
                _status_display(str(status)),
                action,
            )
        else:
            values = (
                item.get("report_claim") or item.get("recorded_claim") or "",
                item.get("evidence_id", ""),
                before_label,
                external_label,
                f"{float(item.get('score', 0.0)):.3f}",
                _status_display(str(status)),
                action,
            )
        lines.append("| " + " | ".join(_md_cell(str(value)) for value in values) + " |")
    if len(lines) == 2:
        lines.append("| - | - | - | - | - | - |" if compact else "| - | - | - | - | - | - | - |")
    elif compact and len(results) > len(visible_results):
        lines.append(f"| ... | ... | ... | ... | ... | showing first {len(visible_results)} of {len(results)}; full JSON has all rows |")
    return lines


def _render_judgment_table(judgments: list[dict[str, Any]], *, compact: bool) -> list[str]:
    if compact:
        header = "| Claim | Evidence ID | Before Label | After Label | Status | Action |"
        separator = "|---|---|---|---|---|---|"
    else:
        header = "| Claim | Evidence ID | Before Label | After Label | Score | Status | Action |"
        separator = "|---|---|---|---|---:|---|---|"
    lines = [header, separator]
    visible_judgments = judgments[:8] if compact else judgments
    for item in visible_judgments:
        status = str(item.get("verification_status", "yellow"))
        if compact:
            values = (
                item.get("report_claim", ""),
                item.get("evidence_id", ""),
                item.get("before_agent_label", ""),
                item.get("external_label", ""),
                _status_display(status),
                item.get("recommended_action", ""),
            )
        else:
            values = (
                item.get("report_claim", ""),
                item.get("evidence_id", ""),
                item.get("before_agent_label", ""),
                item.get("external_label", ""),
                f"{float(item.get('score', 0.0) or 0.0):.3f}",
                _status_display(status),
                item.get("recommended_action", ""),
            )
        lines.append("| " + " | ".join(_md_cell(str(value)) for value in values) + " |")
    if len(lines) == 2:
        lines.append("| - | - | - | - | - | - |" if compact else "| - | - | - | - | - | - | - |")
    elif compact and len(judgments) > len(visible_judgments):
        lines.append(f"| ... | ... | ... | ... | ... | showing first {len(visible_judgments)} of {len(judgments)}; full JSON has all rows |")
    return lines


def _self_confidence_judgments(text: str) -> list[dict[str, Any]]:
    judgments: list[dict[str, Any]] = []
    for claim in _extract_self_confidence_claims(text):
        refs = parse_evidence_references(claim)
        has_no_evidence = bool(NO_EVIDENCE_RE.search(claim))
        clean_claim = NO_EVIDENCE_RE.sub("", claim)
        clean_claim = EVIDENCE_REF_RE.sub("", clean_claim)
        clean_claim = _strip_inline_markdown(clean_claim)
        if not clean_claim:
            continue
        if refs:
            evidence_id = ", ".join(str(ref["evidence_id"]) for ref in refs)
            labels = [str(ref.get("claimed_level") or "supported") for ref in refs]
            before_label = "self_" + "+".join(labels)
            status = "yellow"
            action = "run_external_check"
        elif has_no_evidence:
            evidence_id = "-"
            before_label = "self_insufficient"
            status = "yellow"
            action = "keep_uncertain"
        else:
            evidence_id = "-"
            before_label = "self_supported_implicit"
            status = "yellow"
            action = "needs_evidence_chain"
        judgments.append(
            {
                "occurrence_id": f"CLAIM-{len(judgments) + 1:03d}",
                "evidence_id": evidence_id,
                "before_agent_label": before_label,
                "external_label": "not_checked",
                "score": 0.0,
                "verification_status": status,
                "recommended_action": action,
                "label_delta": f"{before_label} -> not_checked / {status}",
                "final_publish_decision": "not_publishable_as_supported",
                "report_claim": clean_claim,
            }
        )
    return judgments


def _extract_self_confidence_claims(text: str) -> list[str]:
    claims: list[str] = []
    for raw_line in strip_code_blocks(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("|") or re.fullmatch(r"[-: ]+", line):
            continue
        lowered = line.lower()
        if lowered.startswith(("user-provided seed papers", "input papers", "references", "bibliography")):
            continue
        if re.match(r"^\d+\.\s+", line) and (
            "http" in lowered
            or "doi" in lowered
            or "arxiv" in lowered
            or "pmcid" in lowered
            or "nature.com" in lowered
        ):
            continue
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        for part in re.split(r"(?<=[.!?。！？])\s+", line):
            candidate = part.strip()
            if len(_strip_inline_markdown(candidate)) >= 18 or parse_evidence_references(candidate) or NO_EVIDENCE_RE.search(candidate):
                claims.append(candidate)
    return claims


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"green": 0, "yellow": 0, "red": 0}
    for item in items:
        status = str(item.get("verification_status", "")).strip().lower()
        if status in counts:
            counts[status] += 1
    return counts


def _status_display(status: str) -> str:
    normalized = str(status or "").strip().lower()
    mapping = {
        "green": "🟢 green",
        "yellow": "🟡 yellow",
        "red": "🔴 red",
    }
    return mapping.get(normalized, normalized or "yellow")


def _verification_guidance(*, layer1: dict[str, Any], layer2: dict[str, Any], metrics: dict[str, Any]) -> str:
    if layer1.get("missing_count", 0) > 0:
        return "Do not publish yet: fix missing or fabricated EVD references, then rerun artifact.evidence_verify."
    if layer1.get("mismatched_count", 0) > 0:
        return "Do not publish yet: correct mismatched [EVD-xxx:level] labels or update the evidence records."
    if layer1.get("retracted_but_cited_count", 0) > 0:
        return "Do not cite retracted evidence as support; replace or downgrade the affected claims."
    if metrics.get("red_error_count", 0) > 0:
        return "Do not publish the draft as-is: remove, rewrite, or replace evidence for red claims, then rerun artifact.evidence_verify."
    if metrics.get("yellow_uncertain_count", 0) > 0:
        return "Revise before publishing: downgrade yellow claims to cautious wording, add stronger evidence, or mark them as unresolved."
    if metrics.get("citation_completeness", 0.0) < 0.8:
        return "Citation coverage is low; add evidence annotations or mark unsupported claims with [NO_EVIDENCE]."
    return "Evidence verification passed at the configured level. Show user_visible_markdown to the user and proceed."


def _write_verification_artifacts(
    quest_root: Path,
    *,
    payload: dict[str, Any],
    verify_markdown: str,
    annotated_report_markdown: str,
    publishable_report_markdown: str,
    comparison_markdown: str | None,
    evidence_table_markdown: str | None,
    artifact_prefix: str,
) -> dict[str, str]:
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", artifact_prefix.strip() or "evidence_verify").strip("-") or "evidence_verify"
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(payload.get("quest_root", "")))[:8]
    if not stamp:
        stamp = "latest"
    root = quest_root / "artifacts" / "evidence" / "verification"
    root.mkdir(parents=True, exist_ok=True)
    base = f"{safe_prefix}-{stamp}"
    verify_md = root / f"{base}.md"
    verify_json = root / f"{base}.json"
    annotated_md = root / f"{base}-annotated-report.md"
    publishable_md = root / f"{base}-publishable-report.md"
    verify_md.write_text(verify_markdown, encoding="utf-8")
    verify_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    annotated_md.write_text(annotated_report_markdown, encoding="utf-8")
    publishable_md.write_text(publishable_report_markdown, encoding="utf-8")
    paths = {
        "verify_md": str(verify_md.relative_to(quest_root)),
        "verify_json": str(verify_json.relative_to(quest_root)),
        "annotated_report_md": str(annotated_md.relative_to(quest_root)),
        "publishable_report_md": str(publishable_md.relative_to(quest_root)),
    }
    if comparison_markdown is not None:
        comparison_md = root / f"{base}-comparison.md"
        comparison_md.write_text(comparison_markdown, encoding="utf-8")
        paths["comparison_md"] = str(comparison_md.relative_to(quest_root))
    if evidence_table_markdown is not None:
        table_md = root / f"{base}-evidence-table.md"
        table_md.write_text(evidence_table_markdown, encoding="utf-8")
        paths["evidence_table_md"] = str(table_md.relative_to(quest_root))
    return paths


if __name__ == "__main__":  # pragma: no cover - exercised through CLI smoke runs.
    raise SystemExit(main())
