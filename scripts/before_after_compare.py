#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from deepscientist.artifact.evidence_verifier import (
    NO_EVIDENCE_RE,
    _estimate_claim_sentence_count,
    parse_evidence_references,
    strip_code_blocks,
)


def report_stats(text: str, verification: dict[str, Any] | None = None) -> dict[str, Any]:
    claim_count = max(_estimate_claim_sentence_count(text), 1)
    refs = parse_evidence_references(text)
    no_evidence_count = len(NO_EVIDENCE_RE.findall(text))
    stats = {
        "claim_sentence_estimate": claim_count,
        "evidence_citation_count": len(refs),
        "no_evidence_count": no_evidence_count,
        "citation_completeness": len(refs) / claim_count,
        "unsupported_visible_rate": no_evidence_count / claim_count,
        "verified_claim_count": 0,
        "green_supported_count": 0,
        "yellow_uncertain_count": 0,
        "red_error_count": 0,
        "green_supported_rate": 0.0,
        "yellow_uncertain_rate": 0.0,
        "red_error_rate": 0.0,
        "semantic_risk_rate": 0.0,
        "downgraded_claim_count": 0,
        "removed_claim_count": 0,
        "final_hallucination_rate": 0.0,
    }
    if verification:
        metrics = verification.get("metrics") if isinstance(verification, dict) else {}
        if isinstance(metrics, dict):
            for key in (
                "verified_claim_count",
                "green_supported_count",
                "yellow_uncertain_count",
                "red_error_count",
                "green_supported_rate",
                "yellow_uncertain_rate",
                "red_error_rate",
                "semantic_risk_rate",
                "downgraded_claim_count",
                "removed_claim_count",
                "final_hallucination_rate",
            ):
                if key in metrics:
                    stats[key] = metrics[key]
        strict_rate = _strict_hallucination_rate(stats)
        stats["semantic_risk_rate"] = strict_rate
        stats["final_hallucination_rate"] = strict_rate
    return stats


def compare(
    before_text: str,
    after_text: str,
    *,
    before_verification: dict[str, Any] | None = None,
    after_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before = report_stats(before_text, before_verification)
    after = report_stats(after_text, after_verification)
    before["detection_method"] = "external_evidence_chain" if before_verification else "agent_self_confidence"
    after["detection_method"] = "external_evidence_chain" if after_verification else "agent_self_confidence"
    before["comparison_hallucination_risk"] = _comparison_risk(before, has_external=before_verification is not None)
    after["comparison_hallucination_risk"] = _comparison_risk(after, has_external=after_verification is not None)
    before_judgments = _claim_judgments(before_verification) or _self_confidence_judgments(before_text)
    after_judgments = _claim_judgments(after_verification) or _self_confidence_judgments(after_text)
    return {
        "ok": True,
        "before": before,
        "after": after,
        "before_claim_judgments": before_judgments,
        "after_claim_judgments": after_judgments,
        "delta": {
            "citation_completeness": after["citation_completeness"] - before["citation_completeness"],
            "evidence_citation_count": after["evidence_citation_count"] - before["evidence_citation_count"],
            "no_evidence_count": after["no_evidence_count"] - before["no_evidence_count"],
            "green_supported_count": after["green_supported_count"] - before["green_supported_count"],
            "yellow_uncertain_count": after["yellow_uncertain_count"] - before["yellow_uncertain_count"],
            "red_error_count": after["red_error_count"] - before["red_error_count"],
            "downgraded_claim_count": after["downgraded_claim_count"] - before["downgraded_claim_count"],
            "removed_claim_count": after["removed_claim_count"] - before["removed_claim_count"],
            "final_hallucination_rate": after["final_hallucination_rate"] - before["final_hallucination_rate"],
            "comparison_hallucination_risk": after["comparison_hallucination_risk"] - before["comparison_hallucination_risk"],
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    before_risk = payload["before"]["comparison_hallucination_risk"]
    after_risk = payload["after"]["comparison_hallucination_risk"]
    risk_delta = payload["delta"]["comparison_hallucination_risk"]
    direction = "decreased" if risk_delta < 0 else "did not decrease"
    lines = [
        "# Hallucination Comparison",
        "",
        f"Evidence-chain hallucination risk {direction}: {_format_value(before_risk)} -> {_format_value(after_risk)}.",
        "",
        "| Item | Before | After | Change |",
        "|---|---:|---:|---:|",
    ]
    lines.append(f"| Detection method | {_md_cell(payload['before']['detection_method'])} | {_md_cell(payload['after']['detection_method'])} | - |")
    lines.append(f"| Hallucination risk | {_format_value(before_risk)} | {_format_value(after_risk)} | {_format_value(risk_delta)} |")
    lines.append(
        f"| {_status_display('green')} / {_status_display('yellow')} / {_status_display('red')} | "
        f"{_gyr(payload['before'])} | {_gyr(payload['after'])} | - |"
    )
    lines.append(
        "| Evidence citations | "
        f"{payload['before']['evidence_citation_count']} | {payload['after']['evidence_citation_count']} | "
        f"{payload['delta']['evidence_citation_count']} |"
    )
    lines.append(
        "| Claim rows | "
        f"{len(payload.get('before_claim_judgments') or [])} | {len(payload.get('after_claim_judgments') or [])} | - |"
    )
    lines.extend(_render_detection_table("Before Detection Table", payload.get("before_claim_judgments") or []))
    lines.extend(_render_detection_table("After Detection Table", payload.get("after_claim_judgments") or []))
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare evidence citation coverage before and after evidence tracking.")
    parser.add_argument("--before-report", required=True, type=Path)
    parser.add_argument("--after-report", required=True, type=Path)
    parser.add_argument("--before-verify-json", type=Path)
    parser.add_argument("--after-verify-json", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args(argv)

    payload = compare(
        args.before_report.read_text(encoding="utf-8"),
        args.after_report.read_text(encoding="utf-8"),
        before_verification=_read_json(args.before_verify_json),
        after_verification=_read_json(args.after_verify_json),
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


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2%}" if abs(value) <= 1 else f"{value:.3f}"
    return str(value)


def _claim_judgments(verification: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not verification:
        return []
    layer2 = verification.get("layer2") if isinstance(verification, dict) else {}
    results = layer2.get("results") if isinstance(layer2, dict) else []
    judgments: list[dict[str, Any]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        before_label = str(item.get("before_agent_label") or item.get("agent_label") or "")
        external_label = str(item.get("external_label") or item.get("nli_label") or "")
        status = str(item.get("verification_status") or "")
        judgments.append(
            {
                "occurrence_id": item.get("occurrence_id") or "",
                "evidence_id": item.get("evidence_id") or "",
                "before_agent_label": before_label,
                "external_label": external_label,
                "score": float(item.get("score") or 0.0),
                "verification_status": status,
                "recommended_action": item.get("recommended_action") or "",
                "label_delta": item.get("label_delta") or f"{before_label} -> {external_label} / {status}",
                "final_publish_decision": item.get("final_publish_decision") or "",
                "report_claim": item.get("report_claim") or item.get("recorded_claim") or "",
            }
        )
    return judgments


def _self_confidence_judgments(text: str, *, limit: int = 8) -> list[dict[str, Any]]:
    judgments: list[dict[str, Any]] = []
    for claim in _extract_claim_sentences(text):
        refs = parse_evidence_references(claim)
        has_no_evidence = bool(NO_EVIDENCE_RE.search(claim))
        clean_claim = NO_EVIDENCE_RE.sub("", claim)
        clean_claim = re.sub(r"\[(EVD-[^\]:\s]+)(?::([a-zA-Z_-]+))?\]", "", clean_claim)
        clean_claim = re.sub(r"\s+", " ", clean_claim).strip(" -*\t")
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
        if len(judgments) >= limit:
            break
    return judgments


def _extract_claim_sentences(text: str) -> list[str]:
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
            or "phys." in lowered
        ):
            continue
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        for part in re.split(r"(?<=[.!?。！？])\s+", line):
            candidate = part.strip()
            if len(candidate) >= 18 or parse_evidence_references(candidate) or NO_EVIDENCE_RE.search(candidate):
                claims.append(candidate)
    return claims


def _comparison_risk(stats: dict[str, Any], *, has_external: bool) -> float:
    if has_external:
        return _strict_hallucination_rate(stats)
    return max(0.0, min(1.0, 1.0 - float(stats.get("citation_completeness", 0.0) or 0.0)))


def _gyr(stats: dict[str, Any]) -> str:
    return f"{stats.get('green_supported_count', 0)}/{stats.get('yellow_uncertain_count', 0)}/{stats.get('red_error_count', 0)}"


def _strict_hallucination_rate(stats: dict[str, Any]) -> float:
    verified = int(stats.get("verified_claim_count", 0) or 0)
    green = int(stats.get("green_supported_count", 0) or 0)
    yellow = int(stats.get("yellow_uncertain_count", 0) or 0)
    red = int(stats.get("red_error_count", 0) or 0)
    denominator = max(verified, green + yellow + red)
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, (yellow + red) / denominator))


def _render_detection_table(title: str, judgments: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"## {title}",
        "",
        "| Claim | Evidence ID | Before Label | After Label | Status | Action |",
        "|---|---|---|---|---|---|",
    ]
    for item in judgments[:8]:
        lines.append(
            "| "
            + " | ".join(
                _md_cell(str(value))
                for value in (
                    item.get("report_claim", ""),
                    item.get("evidence_id", ""),
                    item.get("before_agent_label", ""),
                    item.get("external_label", ""),
                    _status_display(str(item.get("verification_status", ""))),
                    item.get("recommended_action", ""),
                )
            )
            + " |"
        )
    if not judgments:
        lines.append("| - | - | - | - | - | - |")
    return lines


def _md_cell(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip().replace("|", "\\|")
    if len(text) > 160:
        text = text[:157].rstrip() + "..."
    return text or "-"


def _status_display(status: str) -> str:
    normalized = str(status or "").strip().lower()
    mapping = {
        "green": "🟢 green",
        "yellow": "🟡 yellow",
        "red": "🔴 red",
    }
    return mapping.get(normalized, normalized or "yellow")


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
