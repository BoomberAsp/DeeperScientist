from __future__ import annotations

from pathlib import Path
import sys


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


def test_evidence_table_loads_records_and_renders_markdown(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_table import load_evidence_records, render_evidence_table_markdown

    quest_root = tmp_path / "quest"
    _write_evidence_file(quest_root, "EVD-demo-001")

    records = load_evidence_records(quest_root)
    assert len(records) == 1
    assert records[0].source_excerpt == "The model reaches 93.2% accuracy."
    markdown = render_evidence_table_markdown(records)
    assert "# Evidence Table" in markdown
    assert "93.2%" in markdown


def test_verify_evidence_reports_layer1_and_heuristic_layer2(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import build_report, render_annotated_report, render_markdown

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    report_text = f"The model reaches 93.2% accuracy [{evidence_id}:supported]."
    payload = build_report(
        quest_root,
        report_text,
        backend="heuristic",
    )
    result = payload["layer2"]["results"][0]
    assert payload["layer1"]["verified_count"] == 1
    assert result["nli_label"] == "entailment"
    assert result["verification_status"] == "green"
    assert result["report_claim"] == "The model reaches 93.2% accuracy."
    assert result["before_agent_label"] == "supported"
    assert result["before_agent_confidence"] == "self_supported"
    assert result["external_label"] == "entailment"
    assert result["label_delta"] == "supported -> entailment / green"
    assert result["hallucination_effect"] == "unchanged_supported"
    assert result["final_publish_decision"] == "keep_as_supported"
    assert payload["metrics"]["citation_completeness"] > 0
    assert payload["metrics"]["green_supported_count"] == 1
    assert payload["metrics"]["final_hallucination_rate"] == 0
    verification_md = render_markdown(payload)
    assert "Hallucination Detection Table" in verification_md
    assert "Before Label" in verification_md
    annotated = render_annotated_report(report_text, payload)
    assert "Hallucination Detection" in annotated
    assert "Before Label" in annotated


def test_verify_evidence_cli_writes_annotated_report(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import main

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)
    report_path = tmp_path / "report.md"
    report_path.write_text(f"The model reaches 93.2% accuracy [{evidence_id}:supported].", encoding="utf-8")
    annotated_path = tmp_path / "annotated.md"
    publishable_path = tmp_path / "publishable.md"

    exit_code = main(
        [
            "--quest-root",
            str(quest_root),
            "--report",
            str(report_path),
            "--nli-backend",
            "heuristic",
            "--annotated-out",
            str(annotated_path),
            "--publishable-out",
            str(publishable_path),
        ]
    )

    assert exit_code == 0
    annotated = annotated_path.read_text(encoding="utf-8")
    assert "Hallucination Detection" in annotated
    assert "Before Label" in annotated
    assert "green" in annotated
    publishable = publishable_path.read_text(encoding="utf-8")
    assert f"[{evidence_id}:supported]" in publishable
    assert "Evidence Revision Notes" not in publishable


def test_reference_sections_are_not_semantic_claim_occurrences(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import build_report

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    payload = build_report(
        quest_root,
        "\n".join(
            [
                f"The model reaches 93.2% accuracy [{evidence_id}:supported].",
                "",
                "## Source map",
                f"- Accuracy source [{evidence_id}:supported]",
            ]
        ),
        backend="heuristic",
    )

    assert payload["layer1"]["total_references"] == 2
    assert payload["layer2"]["claim_occurrence_count"] == 1
    assert len(payload["layer2"]["results"]) == 1


def test_before_after_compare_improves_citation_completeness() -> None:
    from before_after_compare import compare, render_markdown

    before = "The model reaches 93.2% accuracy. It is faster."
    after = "The model reaches 93.2% accuracy [EVD-demo-001:supported]. It is faster [NO_EVIDENCE]."
    verification = {
        "metrics": {
            "verified_claim_count": 1,
            "yellow_uncertain_count": 1,
            "final_hallucination_rate": 0,
        },
        "layer2": {
            "results": [
                {
                    "occurrence_id": "CLAIM-001",
                    "evidence_id": "EVD-demo-001",
                    "before_agent_label": "supported",
                    "external_label": "neutral",
                    "score": 0.42,
                    "verification_status": "yellow",
                    "recommended_action": "downgrade",
                    "label_delta": "supported -> neutral / yellow",
                    "final_publish_decision": "keep_only_with_cautious_wording",
                    "report_claim": "The model reaches 93.2% accuracy.",
                }
            ]
        },
    }
    payload = compare(before, after, after_verification=verification)
    assert payload["after"]["evidence_citation_count"] == 1
    assert payload["delta"]["citation_completeness"] > 0
    assert payload["before"]["detection_method"] == "agent_self_confidence"
    assert payload["after"]["detection_method"] == "external_evidence_chain"
    assert payload["after"]["final_hallucination_rate"] == 1
    assert payload["after"]["comparison_hallucination_risk"] == 1
    assert payload["before_claim_judgments"][0]["before_agent_label"] == "self_supported_implicit"
    assert "green_supported_count" in payload["after"]
    assert payload["after_claim_judgments"][0]["before_agent_label"] == "supported"
    markdown = render_markdown(payload)
    assert "After Detection Table" in markdown
    assert "neutral" in markdown


def test_report_sentence_is_verified_instead_of_recorded_claim(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import build_report, render_publishable_report

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    report_text = f"The model reaches 99.9% accuracy [{evidence_id}:supported]."
    payload = build_report(
        quest_root,
        report_text,
        backend="heuristic",
    )

    result = payload["layer2"]["results"][0]
    assert result["report_claim"] == "The model reaches 99.9% accuracy."
    assert result["recorded_claim"] == "The model reaches 93.2% accuracy."
    assert result["verification_status"] == "yellow"
    assert result["recommended_action"] == "downgrade"
    assert payload["metrics"]["final_hallucination_rate"] == 1
    publishable = render_publishable_report(report_text, payload)
    assert f"[{evidence_id}:supported]" not in publishable
    assert "[NO_EVIDENCE]" in publishable
    assert "Evidence Revision Notes" in publishable
    annotated_input = report_text + "\n\n## Hallucination Detection\n\n| stale | table |\n|---|---|\n"
    stripped_publishable = render_publishable_report(annotated_input, payload)
    assert "stale" not in stripped_publishable
    assert "Hallucination Detection" not in stripped_publishable


def test_missing_evidence_reference_is_red(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import build_report, render_publishable_report

    quest_root = tmp_path / "quest"
    report_text = "The paper proves the claim [EVD-missing-001:supported]."
    payload = build_report(
        quest_root,
        report_text,
        backend="heuristic",
    )

    result = payload["layer2"]["results"][0]
    assert result["verification_status"] == "red"
    assert result["recommended_action"] == "remove"
    assert payload["metrics"]["red_error_count"] == 1
    assert payload["metrics"]["final_hallucination_rate"] == 1
    publishable = render_publishable_report(report_text, payload)
    assert "[EVD-missing-001:supported]" not in publishable
    assert "[NO_EVIDENCE]" in publishable


def test_contradictory_report_claim_is_red(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import build_report

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    payload = build_report(
        quest_root,
        f"The model does not reach 93.2% accuracy [{evidence_id}:supported].",
        backend="heuristic",
    )

    result = payload["layer2"]["results"][0]
    assert result["nli_label"] == "contradiction"
    assert result["verification_status"] == "red"
    assert result["recommended_action"] == "replace_evidence"


def _write_evidence_file(quest_root: Path, evidence_id: str) -> None:
    evidence_root = quest_root / "artifacts" / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / f"{evidence_id}.md").write_text(
        f"---\n"
        f"evidence_id: {evidence_id}\n"
        f"title: Accuracy\n"
        f"source_type: experiment_result\n"
        f"source_location: experiments/main/result.json accuracy\n"
        f"claim: The model reaches 93.2% accuracy.\n"
        f"evidence_level: supported\n"
        f"timestamp: now\n"
        f"---\n"
        f"# {evidence_id}: Accuracy\n\n"
        f"## Source Excerpt\n"
        f"> The model reaches 93.2% accuracy.\n\n"
        f"## Claim\n"
        f"The model reaches 93.2% accuracy.\n",
        encoding="utf-8",
    )



def test_api_env_file_and_model_override(tmp_path: Path) -> None:
    from deepscientist.artifact.evidence_verifier import _load_api_config

    env_file = tmp_path / ".env"
    env_file.write_text(
        "NLI_API_KEY=test-key\n"
        "NLI_API_BASE_URL=https://example.test/v1\n"
        "NLI_API_MODEL=env-model\n",
        encoding="utf-8",
    )
    config = _load_api_config(env_file=env_file, model="cli-model")
    assert config["NLI_API_KEY"] == "test-key"
    assert config["NLI_API_BASE_URL"] == "https://example.test/v1"
    assert config["NLI_API_MODEL"] == "cli-model"


def test_parse_api_nli_response_accepts_json_fence() -> None:
    from deepscientist.artifact.evidence_verifier import _parse_api_nli_response

    payload = {
        "choices": [
            {
                "message": {
                    "content": '```json\n{"label":"contradiction","score":0.91,"rationale":"negation conflict"}\n```'
                }
            }
        ]
    }
    parsed = _parse_api_nli_response(payload)
    assert parsed["label"] == "contradiction"
    assert parsed["score"] == 0.91
    assert parsed["rationale"] == "negation conflict"


def test_call_api_nli_uses_openai_compatible_payload() -> None:
    from deepscientist.artifact.evidence_table import EvidenceRecord
    from deepscientist.artifact.evidence_verifier import _call_api_nli

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"label":"entailment","score":0.88,"rationale":"direct match"}'
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self) -> None:
            self.url = ""
            self.headers = {}
            self.payload = {}

        def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
            self.url = url
            self.headers = headers
            self.payload = json
            return FakeResponse()

    client = FakeClient()
    record = EvidenceRecord(
        evidence_id="EVD-demo-001",
        title="Accuracy",
        claim="The model reaches 93.2% accuracy.",
        evidence_level="supported",
        source_type="experiment_result",
        source_location="result.json",
        source_excerpt="The model reaches 93.2% accuracy.",
        claim_relation="direct",
        path="artifacts/evidence/EVD-demo-001.md",
        timestamp="now",
    )
    result = _call_api_nli(
        client,
        record,
        config={
            "NLI_API_KEY": "test-key",
            "NLI_API_BASE_URL": "https://example.test/v1",
            "NLI_API_CHAT_PATH": "/chat/completions",
            "NLI_API_MODEL": "test-model",
        },
    )
    assert client.url == "https://example.test/v1/chat/completions"
    assert client.headers["Authorization"] == "Bearer test-key"
    assert client.payload["model"] == "test-model"
    assert "Source excerpt" in client.payload["messages"][1]["content"]
    assert result.backend == "api"
    assert result.nli_label == "entailment"
    assert result.score == 0.88



def test_cascade_records_heuristic_nli_and_skipped_api(tmp_path: Path, monkeypatch) -> None:
    import deepscientist.artifact.evidence_verifier as ve

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    def fake_transformers(records, *, model, model_source="huggingface", modelscope_model=None):
        return [
            ve.NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="neutral",
                score=0.77,
                backend="transformers",
                rationale="fake nli uncertainty",
            )
            for record in records
        ]

    monkeypatch.setattr(ve, "_run_transformers_nli", fake_transformers)
    payload = ve.build_report(
        quest_root,
        f"The model reaches 93.2% accuracy [{evidence_id}:supported].",
        backend="cascade",
    )
    result = payload["layer2"]["results"][0]
    assert result["backend"] == "cascade"
    assert result["nli_label"] == "neutral"
    assert result["stages"]["heuristic"]["label"] == "entailment"
    assert result["stages"]["nli"]["label"] == "neutral"
    assert result["stages"]["llm_api"]["label"] == "skipped"


def test_cascade_api_final_review_can_override_nli(tmp_path: Path, monkeypatch) -> None:
    import deepscientist.artifact.evidence_verifier as ve

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    def fake_transformers(records, *, model, model_source="huggingface", modelscope_model=None):
        return [
            ve.NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="entailment",
                score=0.83,
                backend="transformers",
                rationale="fake nli support",
            )
            for record in records
        ]

    def fake_api(records, *, model, env_file):
        return [
            ve.NliResult(
                evidence_id=record.evidence_id,
                agent_label=record.evidence_level,
                nli_label="contradiction",
                score=0.92,
                backend="api",
                rationale="fake api contradiction",
            )
            for record in records
        ]

    monkeypatch.setattr(ve, "_run_transformers_nli", fake_transformers)
    monkeypatch.setattr(ve, "_run_api_nli", fake_api)
    payload = ve.build_report(
        quest_root,
        f"The model reaches 93.2% accuracy [{evidence_id}:supported].",
        backend="cascade",
        cascade_api=True,
    )
    result = payload["layer2"]["results"][0]
    assert result["nli_label"] == "contradiction"
    assert result["score"] == 0.92
    assert result["stages"]["llm_api"]["label"] == "contradiction"
    assert "Final label selected from api" in result["rationale"]



def test_modelscope_model_source_uses_snapshot_download(monkeypatch, tmp_path: Path) -> None:
    import sys
    import types
    import deepscientist.artifact.evidence_verifier as ve

    calls = []
    module = types.ModuleType("modelscope")

    def fake_snapshot_download(model_id: str) -> str:
        calls.append(model_id)
        return str(tmp_path / "cached-model")

    module.snapshot_download = fake_snapshot_download
    monkeypatch.setitem(sys.modules, "modelscope", module)

    resolved = ve._resolve_transformers_model(
        model=None,
        model_source="modelscope",
        modelscope_model="demo/nli-model",
    )
    assert resolved == str(tmp_path / "cached-model")
    assert calls == ["demo/nli-model"]



def test_artifact_service_integrated_evidence_verify_returns_user_visible_markdown(tmp_path: Path) -> None:
    from deepscientist.artifact.service import ArtifactService

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)
    service = ArtifactService(tmp_path)

    result = service.verify_evidence_claims(
        quest_root,
        agent_output_text=f"The model reaches 93.2% accuracy [{evidence_id}:supported].",
        verification_mode="none",
        write_artifacts=True,
    )

    assert result["ok"] is True
    assert result["verified_count"] == 1
    assert result["summary"]["verified_count"] == 1
    assert "Evidence Verification Summary" in result["user_visible_markdown"]
    assert result["artifact_paths"]["verify_md"].startswith("artifacts/evidence/verification/")
    assert result["artifact_paths"]["publishable_report_md"].startswith("artifacts/evidence/verification/")
    assert (quest_root / result["artifact_paths"]["verify_md"]).exists()
    assert (quest_root / result["artifact_paths"]["publishable_report_md"]).exists()


def test_artifact_service_evidence_verify_returns_one_turn_comparison_markdown(tmp_path: Path) -> None:
    from deepscientist.artifact.service import ArtifactService

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)
    service = ArtifactService(tmp_path)

    result = service.verify_evidence_claims(
        quest_root,
        before_output_text="The model reaches 93.2% accuracy. It is faster than the baseline system.",
        agent_output_text=f"The model reaches 93.2% accuracy [{evidence_id}:supported].",
        verification_mode="heuristic",
        comparison_mode=True,
        write_artifacts=True,
    )

    assert result["ok"] is True
    assert result["before_detection"]["metrics"]["yellow_uncertain_count"] == 2
    assert result["before_detection"]["metrics"]["final_hallucination_rate"] == 1
    assert result["metrics"]["green_supported_count"] == 1
    assert result["metrics"]["final_hallucination_rate"] == 0
    assert "Before Hallucination Table" in result["comparison_markdown"]
    assert "After Evidence-Chain Hallucination Table" in result["comparison_markdown"]
    assert "Final After Report" in result["comparison_markdown"]
    assert "🟢 green" in result["comparison_markdown"]
    assert "🟡 yellow" in result["comparison_markdown"]
    assert "comparison_md" in result["artifact_paths"]
    assert (quest_root / result["artifact_paths"]["comparison_md"]).exists()
