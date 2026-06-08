"""Source Content Fidelity Check.

Independently fetch source content and verify that the Agent-provided
``source_excerpt`` faithfully appears in the original source.  This bridges the
gap between "the excerpt supports the claim" (Layer 2 NLI) and "the excerpt is
real" — i.e. the Agent didn't fabricate or distort the quote.

Architecture
------------
* ``fetch_source_content()`` — dispatch by ``source_type`` (arXiv, URL, local file).
* ``check_excerpt_fidelity()`` — sliding-window fuzzy match of excerpt in content.
* ``source_fidelity_check()`` — batch runner, called from the evidence verifier.
* Fetched content is cached under ``artifacts/evidence/sources/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .evidence_table import EvidenceRecord

CACHE_DIR_NAME = "sources"
FIDELITY_THRESHOLD = 0.85          # sliding-window similarity threshold
PARTIAL_THRESHOLD = 0.60           # below this → not_found

_ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/abs/|arxiv\.org/pdf/)?(\d{4}\.\d{4,}(?:v\d+)?)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_source_content(
    source_type: str,
    source_location: str,
    *,
    quest_root: Path | None = None,
    cache_dir: Path | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch source content and return ``{ok, content, error, ...}``.

    Cached results are stored keyed by ``sha256(source_location)`` so the same
    source is never fetched twice.
    """
    source_type = (source_type or "").strip().lower()
    source_location = (source_location or "").strip()
    if not source_location:
        return {"ok": False, "error": "source_location is empty.", "content": ""}

    cache_key = hashlib.sha256(source_location.encode()).hexdigest()
    cache_path = _cache_path(cache_dir, cache_key) if cache_dir else None

    if cache_path and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    result = _do_fetch(
        source_type=source_type,
        source_location=source_location,
        quest_root=quest_root,
        timeout=timeout,
    )

    if cache_path and result.get("ok"):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    return result


def check_excerpt_fidelity(
    source_excerpt: str,
    source_content: str,
    *,
    threshold: float = FIDELITY_THRESHOLD,
    partial_threshold: float = PARTIAL_THRESHOLD,
) -> dict[str, Any]:
    """Check whether *source_excerpt* appears in *source_content*.

    Uses a sliding-window fuzzy match: a window the length of the excerpt
    slides over the source text and the best ``SequenceMatcher`` ratio is
    kept.

    Returns
    -------
    dict with keys:
        label   — "verified" | "partial_match" | "not_found"
        score   — best similarity ratio [0.0 – 1.0]
        matched — best matching snippet (empty if not_found)
        position — character offset of the best match (-1 if not_found)
    """
    excerpt = (source_excerpt or "").strip()
    content = (source_content or "").strip()
    if not excerpt:
        return {"label": "unverifiable", "score": 0.0, "matched": "", "position": -1}
    if not content:
        return {"label": "source_unavailable", "score": 0.0, "matched": "", "position": -1}

    # Fast path: exact substring
    idx = content.find(excerpt)
    if idx >= 0:
        return {
            "label": "verified",
            "score": 1.0,
            "matched": excerpt,
            "position": idx,
        }

    # Sliding window fuzzy match
    window_size = len(excerpt)
    content_len = len(content)
    if window_size >= content_len:
        ratio = SequenceMatcher(None, excerpt, content).ratio()
        label = "verified" if ratio >= threshold else ("partial_match" if ratio >= partial_threshold else "not_found")
        return {"label": label, "score": ratio, "matched": content, "position": 0}

    best_ratio = 0.0
    best_pos = -1
    best_snippet = ""
    step = max(1, window_size // 4)  # slide by 25 % of window to balance speed/accuracy
    for start in range(0, content_len - window_size + 1, step):
        snippet = content[start : start + window_size]
        ratio = SequenceMatcher(None, excerpt, snippet).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = start
            best_snippet = snippet

    # Refine around best position
    for delta in range(-step + 1, step):
        start = best_pos + delta
        if start < 0 or start + window_size > content_len:
            continue
        snippet = content[start : start + window_size]
        ratio = SequenceMatcher(None, excerpt, snippet).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = start
            best_snippet = snippet

    if best_ratio >= threshold:
        label = "verified"
    elif best_ratio >= partial_threshold:
        label = "partial_match"
    else:
        label = "not_found"

    return {"label": label, "score": best_ratio, "matched": best_snippet, "position": best_pos}


def source_fidelity_check(
    records: list[EvidenceRecord],
    quest_root: Path,
    *,
    skip_fetch: bool = False,
) -> dict[str, Any]:
    """Run source fidelity check on every evidence record.

    Returns ``{ok, total, results: [{evidence_id, source_type, source_location,
    label, score, matched, position, error}]}``.
    """
    evidence_root = quest_root / "artifacts" / "evidence"
    cache_dir = evidence_root / CACHE_DIR_NAME

    results: list[dict[str, Any]] = []
    for record in records:
        result = {
            "evidence_id": record.evidence_id,
            "source_type": record.source_type,
            "source_location": record.source_location,
        }
        if skip_fetch:
            result.update({"label": "skipped", "score": 0.0, "matched": "", "position": -1})
            results.append(result)
            continue

        if record.evidence_level in {"insufficient", "retracted"}:
            result.update({"label": "skipped", "score": 0.0, "matched": "", "position": -1,
                           "rationale": f"evidence_level is {record.evidence_level}; fidelity check not applicable."})
            results.append(result)
            continue

        if not record.source_excerpt.strip():
            result.update({"label": "unverifiable", "score": 0.0, "matched": "", "position": -1,
                           "rationale": "source_excerpt is empty."})
            results.append(result)
            continue

        fetch_result = fetch_source_content(
            source_type=record.source_type,
            source_location=record.source_location,
            quest_root=quest_root,
            cache_dir=cache_dir,
        )

        if not fetch_result.get("ok"):
            result.update({
                "label": "source_unavailable",
                "score": 0.0,
                "matched": "",
                "position": -1,
                "error": fetch_result.get("error", ""),
                "rationale": f"Could not fetch source: {fetch_result.get('error', 'unknown error')}",
            })
            results.append(result)
            continue

        fidelity = check_excerpt_fidelity(record.source_excerpt, fetch_result["content"])
        result.update(fidelity)
        results.append(result)

    return {
        "ok": True,
        "total": len(records),
        "results": results,
        "verified_count": sum(1 for r in results if r.get("label") == "verified"),
        "partial_count": sum(1 for r in results if r.get("label") == "partial_match"),
        "not_found_count": sum(1 for r in results if r.get("label") == "not_found"),
        "unavailable_count": sum(1 for r in results if r.get("label") in ("source_unavailable", "unverifiable")),
        "skipped_count": sum(1 for r in results if r.get("label") == "skipped"),
    }


# ---------------------------------------------------------------------------
# Internal fetch dispatchers
# ---------------------------------------------------------------------------

def _do_fetch(
    *,
    source_type: str,
    source_location: str,
    quest_root: Path | None,
    timeout: int,
) -> dict[str, Any]:
    if source_type == "arxiv":
        return _fetch_arxiv(source_location, timeout=timeout)
    if source_type == "url":
        return _fetch_url(source_location, timeout=timeout)
    if source_type in {"pdf", "code_output", "bash_log", "experiment_result", "dataset"}:
        return _fetch_local(source_location, quest_root=quest_root)
    # tool_call, memory_card, user_upload, literature_review — no generic fetch strategy
    return {"ok": False, "error": f"source_type '{source_type}' has no automated fetch backend.", "content": ""}


def _fetch_arxiv(paper_id_or_url: str, *, timeout: int) -> dict[str, Any]:
    match = _ARXIV_ID_RE.search(paper_id_or_url)
    paper_id = match.group(1) if match else paper_id_or_url.strip()
    try:
        from .arxiv import read_arxiv_content
    except ImportError:
        return {"ok": False, "error": "arxiv module not available.", "content": ""}

    arxiv_result = read_arxiv_content(paper_id, full_text=False)
    if not arxiv_result.get("ok"):
        return {"ok": False, "error": arxiv_result.get("error", "arXiv fetch failed."), "content": ""}

    # Build a searchable text block from abstract + metadata
    parts = [arxiv_result.get("title", "")]
    parts.append(arxiv_result.get("abstract", ""))
    content = "\n\n".join(part for part in parts if part)
    return {"ok": True, "content": content, "source_description": f"arXiv:{paper_id}"}


def _fetch_url(url: str, *, timeout: int) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DeepScientist/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception as exc:
        return {"ok": False, "error": f"HTTP fetch failed: {exc}", "content": ""}

    content_type = ""
    try:
        content_type = str(resp.headers.get("Content-Type", "") or resp.headers.get("content-type", ""))
    except Exception:
        pass

    if "text/html" in content_type:
        text = _html_to_text(raw.decode("utf-8", errors="replace"))
    elif "text/" in content_type or "application/json" in content_type:
        text = raw.decode("utf-8", errors="replace")
    else:
        return {"ok": False, "error": f"Unsupported content type: {content_type}", "content": ""}

    return {"ok": True, "content": text, "source_description": url}


def _fetch_local(location: str, *, quest_root: Path | None) -> dict[str, Any]:
    path = Path(location)
    if not path.is_absolute() and quest_root is not None:
        path = quest_root / location
    if not path.exists():
        return {"ok": False, "error": f"Local file not found: {path}", "content": ""}
    try:
        return {"ok": True, "content": path.read_text(encoding="utf-8", errors="replace"), "source_description": str(path)}
    except Exception as exc:
        return {"ok": False, "error": f"Could not read local file: {exc}", "content": ""}


def _html_to_text(html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _cache_path(cache_dir: Path | None, cache_key: str) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / f"{cache_key}.json"
