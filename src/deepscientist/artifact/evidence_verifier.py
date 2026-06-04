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
    cascade_api: bool = False,
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
    api_results = _run_api_nli(records, model=model, env_file=env_file) if cascade_api else [
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
        final = _choose_cascade_final(heuristic=heuristic, transformer=transformer, api=api)
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


def _choose_cascade_final(*, heuristic: NliResult, transformer: NliResult, api: NliResult) -> NliResult:
    if api.nli_label not in {"skipped"}:
        return api
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
        f"llm_api={api.nli_label} ({api.score:.3f})",
    ]
    return "; ".join(parts) + f". Final label selected from {final.backend}."


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
    try:
        from modelscope import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError("Python package `modelscope` is not installed. Install it with `pip install modelscope`.") from exc
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

def _run_api_nli(records: list[EvidenceRecord], *, model: str | None, env_file: Path | None) -> list[NliResult]:
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
        for record in records:
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
                results.append(_call_api_nli(client, record, config=config))
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


def _call_api_nli(client: Any, record: EvidenceRecord, *, config: dict[str, str]) -> NliResult:
    url = _join_url(config["NLI_API_BASE_URL"], config.get("NLI_API_CHAT_PATH") or "/chat/completions")
    payload = {
        "model": config["NLI_API_MODEL"],
        "temperature": float(config.get("NLI_API_TEMPERATURE") or "0"),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an external NLI verifier. Decide whether the source excerpt entails, contradicts, "
                    "or is neutral toward the claim. Return strict JSON only with keys: "
                    "label, score, rationale. label must be one of entailment, neutral, contradiction."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Claim:\n{record.claim}\n\n"
                    f"Source excerpt:\n{record.source_excerpt}\n\n"
                    "Classify the relation."
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


def compute_metrics(report_text: str, records: list[EvidenceRecord], nli_results: list[NliResult]) -> dict[str, Any]:
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
    hallucinations = [
        item
        for item in comparable
        if item.agent_label == "supported" and item.nli_label in {"neutral", "contradiction", "unverifiable"}
    ]
    unverifiable = [item for item in nli_results if item.nli_label in {"unverifiable", "skipped"}]
    claim_total = _estimate_claim_sentence_count(report_text)
    cited_claims = len(parse_evidence_references(report_text))
    no_evidence_claims = len(NO_EVIDENCE_RE.findall(strip_code_blocks(report_text)))
    denominator = max(claim_total, cited_claims + no_evidence_claims, 1)
    return {
        "agent_nli_agreement_rate": _ratio(len(agreements), len(comparable)),
        "hallucination_rate": _ratio(len(hallucinations), len(comparable)),
        "unverifiable_rate": _ratio(len(unverifiable), len(nli_results)),
        "citation_completeness": _ratio(cited_claims, denominator),
        "claim_sentence_estimate": denominator,
        "evidence_citation_count": cited_claims,
        "no_evidence_count": no_evidence_claims,
    }


def build_report(
    quest_root: Path,
    report_text: str,
    *,
    backend: str,
    model: str | None = None,
    env_file: Path | None = None,
    cascade_api: bool = False,
    model_source: str = "modelscope",
    modelscope_model: str | None = None,
) -> dict[str, Any]:
    records = load_evidence_records(quest_root)
    layer1 = layer1_verify(report_text, records)
    nli_results = run_nli(
        records,
        backend=backend,
        model=model,
        env_file=env_file,
        cascade_api=cascade_api,
        model_source=model_source,
        modelscope_model=modelscope_model,
    )
    return {
        "ok": True,
        "quest_root": str(quest_root),
        "evidence_total": len(records),
        "layer1": layer1,
        "layer2": {
            "backend": backend,
            "results": [asdict(item) for item in nli_results],
        },
        "metrics": compute_metrics(report_text, records, nli_results),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    lines = [
        "# Evidence Verification Report",
        "",
        f"Quest root: `{payload['quest_root']}`",
        f"Evidence records: {payload['evidence_total']}",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in ("agent_nli_agreement_rate", "hallucination_rate", "unverifiable_rate", "citation_completeness"):
        lines.append(f"| {key} | {metrics[key]:.2%} |")
    lines.extend(["", "## Layer 1 Citation Check", ""])
    layer1 = payload["layer1"]
    lines.extend(
        [
            f"- Total references: {layer1['total_references']}",
            f"- Verified: {layer1['verified_count']}",
            f"- Mismatched: {layer1['mismatched_count']}",
            f"- Missing: {layer1['missing_count']}",
            f"- Retracted but cited: {layer1['retracted_but_cited_count']}",
            "",
            "## Layer 2 NLI Check",
            "",
            "| Evidence ID | Agent Label | NLI Label | Score | Backend | Rationale |",
            "|---|---|---|---:|---|---|",
        ]
    )
    for item in payload["layer2"]["results"]:
        lines.append(
            "| "
            + " | ".join(
                _md_cell(str(value))
                for value in (
                    item["evidence_id"],
                    item["agent_label"],
                    item["nli_label"],
                    f"{float(item['score']):.3f}",
                    item.get("backend", ""),
                    item["rationale"],
                )
            )
            + " |"
        )
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
    parser.add_argument("--cascade-api", action="store_true", help="With --nli-backend cascade, run the final optional LLM API review after heuristic and NLI stages.")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
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
    if not args.json_out and not args.md_out:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]*|\d+(?:\.\d+)?%?|[\u4e00-\u9fff]", text or "")
        if len(token) > 1 and token.lower() not in STOPWORDS
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
    cascade_api: bool = False,
    model_source: str = "modelscope",
    model: str | None = None,
    modelscope_model: str | None = "cross-encoder/nli-roberta-base",
    env_file: Path | str | None = Path(".env"),
    write_artifacts: bool = True,
    artifact_prefix: str = "evidence_verify",
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
    artifact_paths: dict[str, str] = {}
    if write_artifacts:
        artifact_paths = _write_verification_artifacts(
            quest_root,
            payload=payload,
            verify_markdown=verify_markdown,
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
    for item in payload["layer2"].get("results", []):
        if item.get("agent_label") == "supported" and item.get("nli_label") in {"neutral", "contradiction", "unverifiable"}:
            risk_items.append(
                f"- Semantic risk: `{item.get('evidence_id')}` is marked supported but verifier returned `{item.get('nli_label')}`."
            )
    lines = [
        "## Evidence Verification Summary",
        "",
        f"- Evidence records: {payload['evidence_total']}",
        f"- Cited references: {layer1['total_references']}",
        f"- Layer 1 verified: {layer1['verified_count']} / {layer1['total_references']}",
        f"- Missing references: {layer1['missing_count']}",
        f"- Level mismatches: {layer1['mismatched_count']}",
        f"- Retracted citations: {layer1['retracted_but_cited_count']}",
        f"- Hallucination risk: {metrics['hallucination_rate']:.2%}",
        f"- Citation completeness: {metrics['citation_completeness']:.2%}",
        "",
        "### Risk Items",
    ]
    lines.extend(risk_items or ["None."])
    return "\n".join(lines).rstrip() + "\n"


def _verification_guidance(*, layer1: dict[str, Any], layer2: dict[str, Any], metrics: dict[str, Any]) -> str:
    if layer1.get("missing_count", 0) > 0:
        return "Do not publish yet: fix missing or fabricated EVD references, then rerun artifact.evidence_verify."
    if layer1.get("mismatched_count", 0) > 0:
        return "Do not publish yet: correct mismatched [EVD-xxx:level] labels or update the evidence records."
    if layer1.get("retracted_but_cited_count", 0) > 0:
        return "Do not cite retracted evidence as support; replace or downgrade the affected claims."
    for item in layer2.get("results", []):
        if item.get("agent_label") == "supported" and item.get("nli_label") in {"neutral", "contradiction", "unverifiable"}:
            return "Review semantic risk items before publishing; downgrade unsupported claims or record stronger evidence."
    if metrics.get("citation_completeness", 0.0) < 0.8:
        return "Citation coverage is low; add evidence annotations or mark unsupported claims with [NO_EVIDENCE]."
    return "Evidence verification passed at the configured level. Show user_visible_markdown to the user and proceed."


def _write_verification_artifacts(
    quest_root: Path,
    *,
    payload: dict[str, Any],
    verify_markdown: str,
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
    verify_md.write_text(verify_markdown, encoding="utf-8")
    verify_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths = {
        "verify_md": str(verify_md.relative_to(quest_root)),
        "verify_json": str(verify_json.relative_to(quest_root)),
    }
    if evidence_table_markdown is not None:
        table_md = root / f"{base}-evidence-table.md"
        table_md.write_text(evidence_table_markdown, encoding="utf-8")
        paths["evidence_table_md"] = str(table_md.relative_to(quest_root))
    return paths
