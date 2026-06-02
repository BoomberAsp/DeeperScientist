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

from verify_evidence import NO_EVIDENCE_RE, _estimate_claim_sentence_count, parse_evidence_references


def report_stats(text: str) -> dict[str, Any]:
    claim_count = max(_estimate_claim_sentence_count(text), 1)
    refs = parse_evidence_references(text)
    no_evidence_count = len(NO_EVIDENCE_RE.findall(text))
    return {
        "claim_sentence_estimate": claim_count,
        "evidence_citation_count": len(refs),
        "no_evidence_count": no_evidence_count,
        "citation_completeness": len(refs) / claim_count,
        "unsupported_visible_rate": no_evidence_count / claim_count,
    }


def compare(before_text: str, after_text: str) -> dict[str, Any]:
    before = report_stats(before_text)
    after = report_stats(after_text)
    return {
        "ok": True,
        "before": before,
        "after": after,
        "delta": {
            "citation_completeness": after["citation_completeness"] - before["citation_completeness"],
            "evidence_citation_count": after["evidence_citation_count"] - before["evidence_citation_count"],
            "no_evidence_count": after["no_evidence_count"] - before["no_evidence_count"],
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Before/After Evidence Coverage Compare",
        "",
        "| Metric | Before | After | Delta |",
        "|---|---:|---:|---:|",
    ]
    for key in ("claim_sentence_estimate", "evidence_citation_count", "no_evidence_count", "citation_completeness", "unsupported_visible_rate"):
        before_value = payload["before"][key]
        after_value = payload["after"][key]
        delta = after_value - before_value if isinstance(before_value, (int, float)) else 0
        lines.append(f"| {key} | {_format_value(before_value)} | {_format_value(after_value)} | {_format_value(delta)} |")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare evidence citation coverage before and after evidence tracking.")
    parser.add_argument("--before-report", required=True, type=Path)
    parser.add_argument("--after-report", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args(argv)

    payload = compare(
        args.before_report.read_text(encoding="utf-8"),
        args.after_report.read_text(encoding="utf-8"),
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


if __name__ == "__main__":
    raise SystemExit(main())
