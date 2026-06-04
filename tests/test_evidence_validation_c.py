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
    from verify_evidence import build_report

    quest_root = tmp_path / "quest"
    evidence_id = "EVD-demo-001"
    _write_evidence_file(quest_root, evidence_id)

    payload = build_report(
        quest_root,
        f"The model reaches 93.2% accuracy [{evidence_id}:supported].",
        backend="heuristic",
    )
    assert payload["layer1"]["verified_count"] == 1
    assert payload["layer2"]["results"][0]["nli_label"] == "entailment"
    assert payload["metrics"]["citation_completeness"] > 0


def test_before_after_compare_improves_citation_completeness() -> None:
    from before_after_compare import compare

    before = "The model reaches 93.2% accuracy. It is faster."
    after = "The model reaches 93.2% accuracy [EVD-demo-001:supported]. It is faster [NO_EVIDENCE]."
    payload = compare(before, after)
    assert payload["after"]["evidence_citation_count"] == 1
    assert payload["delta"]["citation_completeness"] > 0


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
    from verify_evidence import _load_api_config

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
    from verify_evidence import _parse_api_nli_response

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
    from verify_evidence import _call_api_nli

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
    import verify_evidence as ve

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
    import verify_evidence as ve

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
    import verify_evidence as ve

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
