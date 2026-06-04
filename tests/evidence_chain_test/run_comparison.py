#!/usr/bin/env python3
"""Evidence Chain Tracking Before/After Comparison Test.

Usage:
  python run_comparison.py setup      # Create quest from test_evidence.md brief
  python run_comparison.py before     # Run scout with evidence tracking DISABLED
  python run_comparison.py after      # Run scout with evidence tracking ENABLED
  python run_comparison.py compare    # Diff before/after + run verify_evidence.py
  python run_comparison.py all        # setup → before → after → compare
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

OUTPUTS = HERE / "outputs"
BEFORE_REPORT = OUTPUTS / "before_report.md"
AFTER_REPORT = OUTPUTS / "after_report.md"

# Quest identity — reused across setup / before / after
QUEST_ID = "evidence-chain-comparison"
DAEMON_URL = os.environ.get("DEEPS_CIENTIST_DAEMON_URL", "http://127.0.0.1:20999")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_prompt() -> str:
    prompt_file = HERE / "test_evidence.md"
    if not prompt_file.exists():
        sys.exit(f"Missing test prompt: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8").strip()


def api(path: str, method: str = "GET", body: dict | None = None) -> Any:
    import urllib.request

    url = f"{DAEMON_URL}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"API {method} {path} → {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Cannot reach daemon at {DAEMON_URL}. Is `ds` running? ({exc})")


def daemon_ok() -> bool:
    try:
        return api("/api/quests", method="GET").get("ok") is True
    except SystemExit:
        return False


def quest_exists() -> bool:
    quests = api("/api/quests", method="GET")
    return QUEST_ID in quests.get("quest_ids", [])


def wait_for_idle(quest_id: str, timeout: int = 600) -> bool:
    """Poll until the quest status is idle."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api(f"/api/quests/{quest_id}/status", method="GET")
        state = status.get("status", "unknown")
        if state == "idle":
            return True
        if state in ("error", "stopped"):
            print(f"Quest entered state '{state}' — aborting wait.")
            return False
        time.sleep(5)
    print(f"Timeout waiting for idle state after {timeout}s.")
    return False


def extract_agent_output(quest_id: str) -> str:
    """Pull the last assistant message from the quest chat history."""
    messages = api(f"/api/quests/{quest_id}/chat/messages", method="GET")
    assistant_msgs = [
        m["content"]
        for m in messages.get("messages", [])
        if m.get("role") == "assistant"
    ]
    if not assistant_msgs:
        sys.exit("No assistant messages found in chat history.")
    return assistant_msgs[-1]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_setup() -> None:
    """Create the quest from test_evidence.md and set the brief."""
    if not daemon_ok():
        sys.exit("Daemon not reachable. Start DeepScientist with `ds` first.")

    prompt_text = read_prompt()

    if quest_exists():
        print(f"Quest '{QUEST_ID}' already exists — skipping creation.")
    else:
        payload = api(
            f"/api/quests",
            method="POST",
            body={"quest_id": QUEST_ID},
        )
        if not payload.get("ok"):
            sys.exit(f"Failed to create quest: {payload}")

    # Write the test prompt as the brief
    quests_root = Path(os.path.expanduser("~/DeepScientist/quests"))
    brief_path = quests_root / QUEST_ID / "brief.md"
    brief_path.write_text(prompt_text, encoding="utf-8")
    print(f"Brief written → {brief_path}")
    print(f"Quest '{QUEST_ID}' ready.")


def _run_quest(*, skip_evidence_tracking: bool) -> str:
    """Send the test prompt as a chat message and wait for the agent to finish."""
    if not daemon_ok():
        sys.exit("Daemon not reachable. Start DeepScientist with `ds` first.")
    if not quest_exists():
        sys.exit(f"Quest '{QUEST_ID}' not found. Run `python run_comparison.py setup` first.")

    label = "BEFORE (no evidence tracking)" if skip_evidence_tracking else "AFTER (with evidence tracking)"
    print(f"\n{'='*60}")
    print(f"  Running: {label}")
    print(f"{'='*60}\n")

    # Set the env var for the "before" run
    env = os.environ.copy()
    if skip_evidence_tracking:
        env["DEEPSCIENTIST_SKIP_EVIDENCE_TRACKING"] = "1"

    # Kick off the scout skill via the chat API
    prompt_text = read_prompt()
    chat_resp = api(
        f"/api/quests/{QUEST_ID}/chat",
        method="POST",
        body={"message": prompt_text},
    )
    print(f"Chat response: {json.dumps(chat_resp, indent=2, ensure_ascii=False)[:500]}...")

    # Wait for the agent to finish
    if not wait_for_idle(QUEST_ID):
        sys.exit("Agent did not finish in time.")

    # Extract the final agent output
    output_text = extract_agent_output(QUEST_ID)
    print(f"\nAgent output ({len(output_text)} chars) captured.\n")
    return output_text


def cmd_before() -> None:
    """Run the quest with evidence tracking DISABLED."""
    text = _run_quest(skip_evidence_tracking=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    BEFORE_REPORT.write_text(text, encoding="utf-8")
    print(f"Before report saved → {BEFORE_REPORT}")


def cmd_after() -> None:
    """Run the quest with evidence tracking ENABLED."""
    text = _run_quest(skip_evidence_tracking=False)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    AFTER_REPORT.write_text(text, encoding="utf-8")
    print(f"After report saved → {AFTER_REPORT}")


def cmd_compare() -> None:
    """Run before_after_compare + verify_evidence on the saved reports."""
    if not BEFORE_REPORT.exists():
        sys.exit(f"Before report missing: {BEFORE_REPORT}. Run `python run_comparison.py before` first.")
    if not AFTER_REPORT.exists():
        sys.exit(f"After report missing: {AFTER_REPORT}. Run `python run_comparison.py after` first.")

    OUTPUTS.mkdir(parents=True, exist_ok=True)
    compare_json = OUTPUTS / "compare.json"
    compare_md = OUTPUTS / "compare.md"

    # 1) Before/After comparison
    print("\n--- Before/After Evidence Coverage Comparison ---\n")
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "before_after_compare.py"),
            "--before-report", str(BEFORE_REPORT),
            "--after-report", str(AFTER_REPORT),
            "--json-out", str(compare_json),
            "--md-out", str(compare_md),
        ],
        check=True,
    )

    # Print summary
    delta = json.loads(compare_json.read_text(encoding="utf-8"))
    print(f"  Citation completeness: {delta['before']['citation_completeness']:.1%} → {delta['after']['citation_completeness']:.1%}  (Δ = +{delta['delta']['citation_completeness']:.1%})")
    print(f"  Evidence citations:    {delta['before']['evidence_citation_count']} → {delta['after']['evidence_citation_count']}  (Δ = +{delta['delta']['evidence_citation_count']})")
    print(f"  [NO_EVIDENCE] markers: {delta['before']['no_evidence_count']} → {delta['after']['no_evidence_count']}  (Δ = +{delta['delta']['no_evidence_count']})")

    # 2) Layer 1 + Layer 2 verification on the AFTER report
    print("\n--- Evidence Verification (Layer 1 + Layer 2) ---\n")
    quests_root = Path(os.path.expanduser("~/DeepScientist/quests"))
    quest_root = quests_root / QUEST_ID
    verify_json = OUTPUTS / "verify.json"
    verify_md = OUTPUTS / "verify.md"

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_evidence.py"),
            "--quest-root", str(quest_root),
            "--report", str(AFTER_REPORT),
            "--json-out", str(verify_json),
            "--md-out", str(verify_md),
        ],
        check=True,
    )

    # Print key metrics
    vresult = json.loads(verify_json.read_text(encoding="utf-8"))
    if vresult.get("ok"):
        m = vresult.get("metrics", {})
        print(f"  Agent-NLI agreement:  {m.get('agent_nli_agreement_rate', 0):.1%}")
        print(f"  Hallucination rate:   {m.get('hallucination_rate', 0):.1%}")
        print(f"  Citation completeness: {m.get('citation_completeness', 0):.1%}")
        l1 = vresult.get("layer1", {})
        print(f"  Layer 1 verified:     {l1.get('verified_count', 0)}/{l1.get('total_references', 0)}")
        print(f"  Layer 1 mismatched:   {l1.get('mismatched_count', 0)}")
        print(f"  Layer 1 missing:      {l1.get('missing_count', 0)}")

    print(f"\nFull reports:")
    print(f"  {compare_md}")
    print(f"  {verify_md}")
    print(f"  {compare_json}")
    print(f"  {verify_json}")


def cmd_all() -> None:
    cmd_setup()
    cmd_before()
    cmd_after()
    cmd_compare()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evidence Chain Tracking Before/After Comparison Test"
    )
    parser.add_argument(
        "command",
        choices=["setup", "before", "after", "compare", "all"],
        help="Which step to run.",
    )
    args = parser.parse_args()

    {
        "setup": cmd_setup,
        "before": cmd_before,
        "after": cmd_after,
        "compare": cmd_compare,
        "all": cmd_all,
    }[args.command]()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
