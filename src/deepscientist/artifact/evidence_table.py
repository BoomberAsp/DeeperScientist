from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import re
import sys
from pathlib import Path
from typing import Any


EVIDENCE_LEVELS = ("supported", "inferred", "insufficient", "retracted")


def load_markdown_document(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to parse evidence frontmatter.") from exc
    metadata = yaml.safe_load(parts[1]) or {}
    return metadata if isinstance(metadata, dict) else {}, parts[2]


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    title: str
    claim: str
    evidence_level: str
    source_type: str
    source_location: str
    source_excerpt: str
    claim_relation: str
    path: str
    timestamp: str


def evidence_root_for(quest_root: Path) -> Path:
    return Path(quest_root) / "artifacts" / "evidence"


def load_evidence_records(quest_root: Path) -> list[EvidenceRecord]:
    root = evidence_root_for(quest_root)
    if not root.exists():
        return []
    records: list[EvidenceRecord] = []
    for path in sorted(root.glob("EVD-*.md")):
        metadata, body = load_markdown_document(path)
        evidence_id = str(metadata.get("evidence_id") or path.stem).strip()
        records.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                title=str(metadata.get("title") or "").strip(),
                claim=str(metadata.get("claim") or "").strip(),
                evidence_level=str(metadata.get("evidence_level") or "").strip(),
                source_type=str(metadata.get("source_type") or "").strip(),
                source_location=str(metadata.get("source_location") or "").strip(),
                source_excerpt=_extract_section(body, "Source Excerpt", blockquote=True),
                claim_relation=_extract_section(body, "Relationship to Claim", blockquote=False),
                path=str(path.relative_to(quest_root)),
                timestamp=str(metadata.get("timestamp") or metadata.get("updated_at") or "").strip(),
            )
        )
    return records


def evidence_records_to_json(records: list[EvidenceRecord]) -> dict[str, Any]:
    by_level = {level: 0 for level in EVIDENCE_LEVELS}
    for record in records:
        if record.evidence_level in by_level:
            by_level[record.evidence_level] += 1
    return {
        "ok": True,
        "total": len(records),
        "by_level": by_level,
        "records": [asdict(record) for record in records],
    }


def render_evidence_table_markdown(records: list[EvidenceRecord]) -> str:
    lines = [
        "# Evidence Table",
        "",
        f"Total evidence records: {len(records)}",
        "",
        "| Evidence ID | Level | Claim | Source | Excerpt |",
        "|---|---|---|---|---|",
    ]
    for record in records:
        source = " ".join(item for item in (record.source_type, record.source_location) if item)
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    record.evidence_id,
                    record.evidence_level,
                    record.claim,
                    source,
                    record.source_excerpt,
                )
            )
            + " |"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_evidence_table(
    quest_root: Path,
    *,
    markdown_path: Path | None = None,
    json_path: Path | None = None,
) -> dict[str, Any]:
    records = load_evidence_records(quest_root)
    payload = evidence_records_to_json(records)
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_evidence_table_markdown(records), encoding="utf-8")
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _extract_section(body: str, heading: str, *, blockquote: bool) -> str:
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*$([\s\S]*?)(?=^## |\Z)",
        flags=re.MULTILINE,
    )
    match = pattern.search(body or "")
    if not match:
        return ""
    text = match.group(1).strip()
    if blockquote:
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(">"):
                stripped = stripped[1:].strip()
            if stripped:
                lines.append(stripped)
        return " ".join(lines).strip()
    return re.sub(r"\s+", " ", text).strip()


def _md_cell(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace("|", "\\|")
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return text or "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render DeepScientist evidence records as Markdown and/or JSON.")
    parser.add_argument("--quest-root", required=True, type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    payload = write_evidence_table(args.quest_root, markdown_path=args.md_out, json_path=args.json_out)
    if not args.md_out and not args.json_out:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
