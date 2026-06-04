"""Unit tests for evidence chain tracking module (Member A)."""

import pytest
from pathlib import Path


_SAMPLE_EXCERPT = "The model achieves 93.2% average accuracy on the GLUE benchmark."


class TestEvidenceSchemas:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from deepscientist.artifact.schemas import validate_evidence_payload
        self.validate = validate_evidence_payload

    def test_validate_valid_payload(self):
        errors = self.validate({
            "source_type": "arxiv",
            "evidence_level": "supported",
            "claim": "BERT achieves 93.2% on GLUE",
            "source_excerpt": _SAMPLE_EXCERPT,
        })
        assert errors == []

    def test_validate_invalid_source_type(self):
        errors = self.validate({
            "source_type": "imagination",
            "claim": "Something",
        })
        assert len(errors) >= 1

    def test_validate_missing_claim(self):
        errors = self.validate({})
        assert any("claim" in e.lower() for e in errors)

    def test_validate_bad_hash_format(self):
        errors = self.validate({
            "source_content_hash": "not-a-sha256-prefix",
            "claim": "Something",
        })
        assert len(errors) >= 1

    def test_valid_hash_accepted(self):
        errors = self.validate({
            "source_content_hash": "sha256:abc123def456",
            "claim": "Something",
        })
        assert errors == []

    # --- source_excerpt conditional requirement ---

    def test_supported_requires_source_excerpt(self):
        errors = self.validate({
            "evidence_level": "supported",
            "claim": "Some claim",
            "source_excerpt": "",
        })
        assert len(errors) >= 1
        assert any("source_excerpt" in e.lower() for e in errors)

    def test_inferred_requires_source_excerpt(self):
        errors = self.validate({
            "evidence_level": "inferred",
            "claim": "Some claim",
            "source_excerpt": "",
        })
        assert len(errors) >= 1
        assert any("source_excerpt" in e.lower() for e in errors)

    def test_insufficient_allows_missing_source_excerpt(self):
        errors = self.validate({
            "evidence_level": "insufficient",
            "claim": "Some claim",
        })
        assert errors == []

    def test_retracted_allows_missing_source_excerpt(self):
        errors = self.validate({
            "evidence_level": "retracted",
            "claim": "Some claim",
        })
        assert errors == []


class TestEvidenceRecord:
    @pytest.fixture
    def quest_dirs(self, tmp_path):
        """Create minimal quest directory structure for evidence methods."""
        quest_root = tmp_path / "quest"
        (quest_root / "artifacts" / "evidence").mkdir(parents=True)
        (quest_root / ".ds").mkdir(parents=True)
        return tmp_path, quest_root

    @pytest.fixture
    def service(self, tmp_path, quest_dirs):
        from deepscientist.artifact.service import ArtifactService
        return ArtifactService(tmp_path)

    def test_record_creates_file_and_updates_index(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        result = service.record_evidence(
            quest_root,
            title="Test evidence",
            source_type="arxiv",
            claim="Test claim",
            evidence_level="supported",
            source_excerpt=_SAMPLE_EXCERPT,
        )
        assert result["ok"] is True
        assert result["evidence_id"].startswith("EVD-")
        evidence_file = quest_root / "artifacts" / "evidence" / f"{result['evidence_id']}.md"
        assert evidence_file.exists()

    def test_record_rejects_supported_without_source_excerpt(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        result = service.record_evidence(
            quest_root,
            title="Bad evidence",
            claim="Claim without source quote",
            evidence_level="supported",
            source_excerpt="",
        )
        assert result["ok"] is False
        assert "errors" in result
        assert any("source_excerpt" in e.lower() for e in result["errors"])

    def test_record_accepts_insufficient_without_source_excerpt(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        result = service.record_evidence(
            quest_root,
            title="Weak evidence",
            claim="Unsupported claim",
            evidence_level="insufficient",
        )
        assert result["ok"] is True

    def test_list_evidence_returns_records(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        service.record_evidence(
            quest_root, title="E1", claim="C1", evidence_level="supported",
            source_excerpt=_SAMPLE_EXCERPT,
        )
        service.record_evidence(
            quest_root, title="E2", claim="C2", evidence_level="inferred",
            source_excerpt=_SAMPLE_EXCERPT,
        )

        result = service.list_evidence(quest_root)
        assert result["ok"] is True
        assert result["total"] == 2

    def test_verify_detects_missing_reference(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        service.record_evidence(
            quest_root, title="E1", claim="C1", evidence_level="supported",
            source_excerpt=_SAMPLE_EXCERPT,
        )
        result = service.verify_evidence_claims(
            quest_root,
            verification_mode="none",
            write_artifacts=False,
            agent_output_text="According to the data [EVD-nonexistent:supported]",
        )
        assert result["ok"] is True
        assert len(result["missing"]) == 1

    def test_verify_detects_retracted_citation(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        record = service.record_evidence(
            quest_root, title="Bad evidence", claim="Wrong claim",
            evidence_level="supported", source_excerpt=_SAMPLE_EXCERPT,
        )
        evd_id = record["evidence_id"]
        # Mark as retracted
        service.update_evidence(quest_root, evidence_id=evd_id, evidence_level="retracted")

        result = service.verify_evidence_claims(
            quest_root,
            verification_mode="none",
            write_artifacts=False,
            agent_output_text=f"The data confirms this [{evd_id}:supported]",
        )
        assert result["ok"] is True
        assert len(result["retracted_but_cited"]) == 1
        assert result["retracted_but_cited"][0]["evidence_id"] == evd_id

    def test_update_evidence_changes_level(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        record = service.record_evidence(
            quest_root, title="E1", claim="C1", evidence_level="inferred",
            source_excerpt=_SAMPLE_EXCERPT,
        )
        evd_id = record["evidence_id"]

        update_result = service.update_evidence(
            quest_root, evidence_id=evd_id, evidence_level="supported",
            source_excerpt="Updated verbatim quote from source.",
        )
        assert update_result["ok"] is True
        assert update_result["evidence_level"] == "supported"

    def test_get_evidence_returns_detail(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        record = service.record_evidence(
            quest_root, title="Detail test", claim="A detailed claim",
            evidence_level="supported", source_excerpt=_SAMPLE_EXCERPT,
        )
        evd_id = record["evidence_id"]

        result = service.get_evidence(quest_root, evd_id)
        assert result["ok"] is True
        assert result["evidence_id"] == evd_id
        assert result["metadata"]["claim"] == "A detailed claim"

    def test_list_evidence_filter_by_level(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        service.record_evidence(
            quest_root, title="E1", claim="C1", evidence_level="supported",
            source_excerpt=_SAMPLE_EXCERPT,
        )
        service.record_evidence(
            quest_root, title="E2", claim="C2", evidence_level="inferred",
            source_excerpt=_SAMPLE_EXCERPT,
        )

        result = service.list_evidence(quest_root, evidence_level="supported")
        assert result["ok"] is True
        assert result["total"] == 1

    def test_index_snapshot(self, quest_dirs, service):
        _tmp_path, quest_root = quest_dirs

        service.record_evidence(
            quest_root, title="E1", claim="C1", evidence_level="supported",
            source_excerpt=_SAMPLE_EXCERPT,
        )

        result = service.index_snapshot(quest_root)
        assert result["ok"] is True
        assert result["evidence_total"] == 1


class TestEvidenceIdempotent:
    """Test that record_evidence is idempotent by evidence_id."""

    @pytest.fixture
    def quest_dirs(self, tmp_path):
        quest_root = tmp_path / "quest"
        (quest_root / "artifacts" / "evidence").mkdir(parents=True)
        (quest_root / ".ds").mkdir(parents=True)
        return tmp_path, quest_root

    def test_same_evidence_id_updates_index_row(self, quest_dirs):
        from deepscientist.artifact.service import ArtifactService
        _tmp_path, quest_root = quest_dirs
        service = ArtifactService(_tmp_path)

        service.record_evidence(
            quest_root, title="V1", claim="C1", evidence_level="supported",
            evidence_id="EVD-test-001", source_excerpt=_SAMPLE_EXCERPT,
        )
        service.record_evidence(
            quest_root, title="V2", claim="C1", evidence_level="inferred",
            evidence_id="EVD-test-001", source_excerpt="Updated quote.",
        )

        result = service.list_evidence(quest_root)
        assert result["total"] == 1  # Not duplicated
        record = result["evidence_records"][0]
        assert record["Title"] == "V2"
        assert record["Evidence Level"] == "inferred"
