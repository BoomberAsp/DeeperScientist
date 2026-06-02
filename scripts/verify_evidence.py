#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from deepscientist.artifact.evidence_table import EvidenceRecord, load_evidence_records


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
        return _run_transformers_nli(records, model=model)
    if backend == "api":
        return _run_api_nli(records, model=model, env_file=env_file)
    return [_heuristic_nli(record) for record in records]


def _run_transformers_nli(records: list[EvidenceRecord], *, model: str | None) -> list[NliResult]:
    try:
        from transformers import pipeline
    except ModuleNotFoundError:
        return [
            NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="skipped",
                score=0.0,
                backend="transformers",
                rationale="Python package `transformers` is not installed; rerun with --nli-backend heuristic or install the model dependencies.",
            )
            for record in records
        ]

    classifier = pipeline(
        "text-classification",
        model=model or "MoritzLaurer/deberta-v3-base-mnli-fever-anli",
        top_k=None,
    )
    results: list[NliResult] = []
    for record in records:
        if not record.source_excerpt.strip():
            results.append(_unverifiable(record, "source_excerpt is missing.", backend="transformers"))
            continue
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
    return results


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
) -> dict[str, Any]:
    records = load_evidence_records(quest_root)
    layer1 = layer1_verify(report_text, records)
    nli_results = run_nli(records, backend=backend, model=model, env_file=env_file)
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
    parser.add_argument("--nli-backend", choices=("heuristic", "transformers", "api", "none"), default="heuristic")
    parser.add_argument("--model", default=None, help="Model override. HuggingFace model for transformers; API model for api.")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env", help="Environment file for --nli-backend api.")
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


if __name__ == "__main__":
    raise SystemExit(main())
