#!/usr/bin/env python3
"""Evidence Chain Tracking Before/After Comparison Test.

Usage:
  python run_comparison.py setup      # Create quest from test_evidence.md brief
  python run_comparison.py before     # Run scout with evidence tracking DISABLED
  python run_comparison.py after      # Run scout with evidence tracking ENABLED
  python run_comparison.py compare    # Diff before/after + run verify_evidence.py
  python run_comparison.py all        # setup -> before -> after -> compare

Existing quest comparison:
  python run_comparison.py compare \
    --name comparison2 \
    --before-quest comparison-2-before \
    --after-quest comparison-2-after
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

DEFAULT_OUTPUTS = REPO_ROOT / "outputs"

# Quest identity — reused across setup / before / after
DEFAULT_QUEST_ID = "evidence-chain-comparison"
DAEMON_URL = os.environ.get("DEEPS_CIENTIST_DAEMON_URL", "http://127.0.0.1:20999")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_prompt(prompt_file: Path | None = None) -> str:
    prompt_file = prompt_file or (HERE / "test_evidence.md")
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
        quests = api("/api/quests", method="GET")
        return isinstance(quests, list)
    except SystemExit:
        return False


def quest_exists(quest_id: str) -> bool:
    quests = api("/api/quests", method="GET")
    if not isinstance(quests, list):
        return False
    return any(str(item.get("quest_id") or "") == quest_id for item in quests if isinstance(item, dict))


def runtime_home() -> Path:
    health = api("/api/health", method="GET")
    home = str(health.get("home") or "").strip() if isinstance(health, dict) else ""
    return Path(home).expanduser() if home else Path(os.path.expanduser("~/DeepScientist"))


def quest_root_for(quest_id: str) -> Path:
    return runtime_home() / "quests" / quest_id


def wait_for_idle(quest_id: str, timeout: int = 600) -> bool:
    """Poll until the quest status is idle."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = api(f"/api/quests/{quest_id}/session", method="GET")
        snapshot = session.get("snapshot") if isinstance(session, dict) else {}
        state = str((snapshot or {}).get("status") or (snapshot or {}).get("runtime_status") or "unknown")
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
    messages = api(f"/api/quests/{quest_id}/history", method="GET")
    assistant_msgs = [
        m["content"]
        for m in messages
        if isinstance(m, dict)
        if m.get("role") == "assistant"
    ]
    if not assistant_msgs:
        sys.exit("No assistant messages found in chat history.")
    return assistant_msgs[-1]


def output_paths(args: argparse.Namespace) -> dict[str, Path]:
    output_dir = Path(args.output_dir).expanduser()
    name = str(args.name or "").strip()
    if name:
        prefix = f"{name}_"
        return {
            "dir": output_dir,
            "before": Path(args.before_report).expanduser() if args.before_report else output_dir / f"{prefix}before.md",
            "after": Path(args.after_report).expanduser() if args.after_report else output_dir / f"{prefix}after.md",
            "compare_json": output_dir / f"{prefix}compare.json",
            "compare_md": output_dir / f"{prefix}compare.md",
            "verify_json": output_dir / f"{prefix}verify.json",
            "verify_md": output_dir / f"{prefix}verify.md",
            "annotated_report_md": output_dir / f"{prefix}annotated_report.md",
            "evidence_table_json": output_dir / f"{prefix}evidence_table.json",
            "evidence_table_md": output_dir / f"{prefix}evidence_table.md",
        }
    return {
        "dir": output_dir,
        "before": Path(args.before_report).expanduser() if args.before_report else output_dir / "before_report.md",
        "after": Path(args.after_report).expanduser() if args.after_report else output_dir / "after_report.md",
        "compare_json": output_dir / "compare.json",
        "compare_md": output_dir / "compare.md",
        "verify_json": output_dir / "verify.json",
        "verify_md": output_dir / "verify.md",
        "annotated_report_md": output_dir / "annotated_report.md",
        "evidence_table_json": output_dir / "evidence_table.json",
        "evidence_table_md": output_dir / "evidence_table.md",
    }


def latest_artifact_report(quest_root: Path, *, preferred_dirs: tuple[str, ...]) -> Path:
    candidates: list[Path] = []
    for dirname in preferred_dirs:
        candidates.extend((quest_root / "artifacts" / dirname).glob("*.md"))
    candidates = [
        path
        for path in candidates
        if path.is_file()
        and not path.name.startswith(".")
        and not path.name.endswith("-evidence-table.md")
    ]
    if not candidates:
        raise FileNotFoundError(f"No Markdown report found under {quest_root}/artifacts/{' or '.join(preferred_dirs)}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def collect_existing_reports(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    paths = output_paths(args)
    paths["dir"].mkdir(parents=True, exist_ok=True)

    before_path = paths["before"]
    after_path = paths["after"]
    after_quest_root: Path | None = None

    if args.before_quest:
        before_root = quest_root_for(args.before_quest)
        source = latest_artifact_report(before_root, preferred_dirs=("reports", "idea"))
        before_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Copied before report: {source} -> {before_path}")

    if args.after_quest:
        after_quest_root = quest_root_for(args.after_quest)
        source = latest_artifact_report(after_quest_root, preferred_dirs=("idea", "reports"))
        after_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Copied after report:  {source} -> {after_path}")

    if after_quest_root is None:
        quest_id = args.quest_id or DEFAULT_QUEST_ID
        after_quest_root = quest_root_for(quest_id)

    return before_path, after_path, after_quest_root


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> None:
    """Create the quest from test_evidence.md and set the brief."""
    if not daemon_ok():
        sys.exit("Daemon not reachable. Start DeepScientist with `ds` first.")

    quest_id = args.quest_id or DEFAULT_QUEST_ID
    prompt_text = read_prompt(args.prompt_file)

    if quest_exists(quest_id):
        print(f"Quest '{quest_id}' already exists — skipping creation.")
        api(
            f"/api/quests/{quest_id}/settings",
            method="PATCH",
            body={"workspace_mode": "copilot"},
        )
    else:
        payload = api(
            f"/api/quests",
            method="POST",
            body={
                "quest_id": quest_id,
                "goal": prompt_text,
                "startup_contract": {
                    "workspace_mode": "copilot",
                    "decision_policy": "user_gated",
                },
            },
        )
        if not payload.get("ok"):
            sys.exit(f"Failed to create quest: {payload}")

    # Write the test prompt as the brief
    brief_path = quest_root_for(quest_id) / "brief.md"
    brief_path.write_text(prompt_text, encoding="utf-8")
    print(f"Brief written → {brief_path}")
    print(f"Quest '{quest_id}' ready.")


def _run_quest(args: argparse.Namespace, *, skip_evidence_tracking: bool) -> str:
    """Send the test prompt as a chat message and wait for the agent to finish."""
    if not daemon_ok():
        sys.exit("Daemon not reachable. Start DeepScientist with `ds` first.")
    quest_id = args.quest_id or DEFAULT_QUEST_ID
    if not quest_exists(quest_id):
        sys.exit(f"Quest '{quest_id}' not found. Run `python run_comparison.py setup` first.")

    label = "BEFORE (no evidence tracking)" if skip_evidence_tracking else "AFTER (with evidence tracking)"
    print(f"\n{'='*60}")
    print(f"  Running: {label}")
    print(f"{'='*60}\n")

    # Set the env var for the "before" run
    env = os.environ.copy()
    if skip_evidence_tracking:
        env["DEEPSCIENTIST_SKIP_EVIDENCE_TRACKING"] = "1"

    # Kick off the scout skill via the chat API
    prompt_text = read_prompt(args.prompt_file)
    chat_resp = api(
        f"/api/quests/{quest_id}/chat",
        method="POST",
        body={"text": prompt_text},
    )
    print(f"Chat response: {json.dumps(chat_resp, indent=2, ensure_ascii=False)[:500]}...")

    # Wait for the agent to finish
    if not wait_for_idle(quest_id):
        sys.exit("Agent did not finish in time.")

    # Extract the final agent output
    output_text = extract_agent_output(quest_id)
    print(f"\nAgent output ({len(output_text)} chars) captured.\n")
    return output_text


def cmd_before(args: argparse.Namespace) -> None:
    """Run the quest with evidence tracking DISABLED."""
    text = _run_quest(args, skip_evidence_tracking=True)
    paths = output_paths(args)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["before"].write_text(text, encoding="utf-8")
    print(f"Before report saved → {paths['before']}")


def cmd_after(args: argparse.Namespace) -> None:
    """Run the quest with evidence tracking ENABLED."""
    text = _run_quest(args, skip_evidence_tracking=False)
    paths = output_paths(args)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["after"].write_text(text, encoding="utf-8")
    print(f"After report saved → {paths['after']}")


def cmd_compare(args: argparse.Namespace) -> None:
    """Run before_after_compare + verify_evidence on the saved reports."""
    before_report, after_report, after_quest_root = collect_existing_reports(args)
    paths = output_paths(args)

    if not before_report.exists():
        sys.exit(f"Before report missing: {before_report}. Run `before` first or pass --before-quest/--before-report.")
    if not after_report.exists():
        sys.exit(f"After report missing: {after_report}. Run `after` first or pass --after-quest/--after-report.")

    if args.include_evidence_table:
        print("\n--- Evidence Table ---\n")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "deepscientist.artifact.evidence_table",
                "--quest-root", str(after_quest_root),
                "--json-out", str(paths["evidence_table_json"]),
                "--md-out", str(paths["evidence_table_md"]),
            ],
            check=True,
            env={**os.environ, "PYTHONPATH": f"{SRC_ROOT}:{REPO_ROOT / 'scripts'}"},
        )
        evidence_table = json.loads(paths["evidence_table_json"].read_text(encoding="utf-8"))
        print(f"  Evidence records: {evidence_table.get('total', 0)}")
        print(f"  By level: {evidence_table.get('by_level', {})}")

    # 1) Layer 1 + Layer 2 verification on the AFTER report.
    print("\n--- Evidence Verification (Layer 1 + Layer 2) ---\n")

    verify_cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "verify_evidence.py"),
        "--quest-root", str(after_quest_root),
        "--report", str(after_report),
        "--nli-backend", args.nli_backend,
        "--model-source", args.model_source,
        "--json-out", str(paths["verify_json"]),
        "--md-out", str(paths["verify_md"]),
        "--annotated-out", str(paths["annotated_report_md"]),
    ]
    if args.model:
        verify_cmd.extend(["--model", args.model])
    if args.modelscope_model:
        verify_cmd.extend(["--modelscope-model", args.modelscope_model])
    if args.env_file:
        verify_cmd.extend(["--env-file", str(args.env_file)])
    if args.cascade_api:
        verify_cmd.append("--cascade-api")

    subprocess.run(verify_cmd, check=True)

    # Print key metrics
    vresult = json.loads(paths["verify_json"].read_text(encoding="utf-8"))
    if vresult.get("ok"):
        m = vresult.get("metrics", {})
        print(f"  Final hallucination risk: {m.get('final_hallucination_rate', m.get('hallucination_rate', 0)):.1%}")
        print(f"  Green/Yellow/Red: {m.get('green_supported_count', 0)}/{m.get('yellow_uncertain_count', 0)}/{m.get('red_error_count', 0)}")

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "before_after_compare.py"),
            "--before-report", str(before_report),
            "--after-report", str(after_report),
            "--after-verify-json", str(paths["verify_json"]),
            "--json-out", str(paths["compare_json"]),
            "--md-out", str(paths["compare_md"]),
        ],
        check=True,
    )

    updated = json.loads(paths["compare_json"].read_text(encoding="utf-8"))
    print(
        "  Comparison hallucination risk: "
        f"{updated['before']['comparison_hallucination_risk']:.1%} -> "
        f"{updated['after']['comparison_hallucination_risk']:.1%}"
    )

    print(f"\nConcise outputs:")
    print(f"  {paths['compare_md']}")
    print(f"  {paths['annotated_report_md']}")


def cmd_all(args: argparse.Namespace) -> None:
    cmd_setup(args)
    cmd_before(args)
    cmd_after(args)
    cmd_compare(args)


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
    parser.add_argument("--quest-id", default=DEFAULT_QUEST_ID, help="Quest id used by setup/before/after, and default after quest root for compare.")
    parser.add_argument("--before-quest", help="Existing quest id to collect the before report from.")
    parser.add_argument("--after-quest", help="Existing quest id to collect the after report and evidence from.")
    parser.add_argument("--before-report", type=Path, help="Existing before report path, or output path when --before-quest is used.")
    parser.add_argument("--after-report", type=Path, help="Existing after report path, or output path when --after-quest is used.")
    parser.add_argument("--name", default="", help="Output name prefix, e.g. comparison2 -> comparison2_before.md.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUTS, help="Directory for copied reports and C-part outputs.")
    parser.add_argument("--prompt-file", type=Path, default=HERE / "test_evidence.md", help="Prompt file used by setup/before/after.")
    parser.add_argument("--nli-backend", choices=("cascade", "heuristic", "transformers", "api", "none"), default="heuristic", help="Verifier backend. heuristic is reproducible and avoids model downloads/API calls.")
    parser.add_argument("--model", default=None, help="NLI/API model override passed through to verify_evidence.py.")
    parser.add_argument("--model-source", choices=("huggingface", "modelscope"), default="modelscope")
    parser.add_argument("--modelscope-model", default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--cascade-api", action="store_true", help="Enable optional API final review in cascade mode.")
    parser.add_argument("--include-evidence-table", action="store_true", help="Also write the full evidence table artifacts. Off by default to keep comparison outputs short.")
    args = parser.parse_args()

    {
        "setup": cmd_setup,
        "before": cmd_before,
        "after": cmd_after,
        "compare": cmd_compare,
        "all": cmd_all,
    }[args.command](args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
