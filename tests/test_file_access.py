"""Tests for the agent-world file-access validator.

The validator enforces the 2026-04-24 boundary design: the production
ReAct agent may read from ``TRIO_BUNDLE_DIR`` and ``AGENT_STATE_DIR`` and
write only to ``AGENT_STATE_DIR``. Audit, raw, staging, and logs zones are
hard-rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ai_assistant.file_access import (
    ZoneViolationError,
    is_agent_readable,
    validate_agent_read,
    validate_agent_write,
    validate_sandbox_write,
)


class TestValidateAgentRead:
    def test_trio_bundle_datasets_allowed(self, monkeypatch_config: Path) -> None:
        import config

        f = config.TRIO_DATASETS_DIR / "sample.jsonl"
        f.write_text("{}\n", encoding="utf-8")
        assert validate_agent_read(f) == Path(f.resolve())

    def test_trio_pdfs_allowed(self, monkeypatch_config: Path) -> None:
        import config

        f = config.PDF_EXTRACTIONS_DIR / "doc.json"
        f.write_text("{}", encoding="utf-8")
        assert validate_agent_read(f) == Path(f.resolve())

    def test_agent_state_allowed(self, monkeypatch_config: Path) -> None:
        import config

        f = config.AGENT_OUTPUT_DIR / "prior_result.csv"
        f.write_text("a,b\n1,2\n", encoding="utf-8")
        assert validate_agent_read(f) == Path(f.resolve())

    def test_study_knowledge_yaml_allowlisted(self) -> None:
        """The repo-tracked YAML is the only allowlisted source-tree file."""
        project_root = Path(__file__).resolve().parents[1]
        yaml_path = project_root / "config" / "study_knowledge.yaml"
        assert validate_agent_read(yaml_path) == Path(yaml_path.resolve())

    def test_audit_rejected(self, monkeypatch_config: Path) -> None:
        import config

        f = config.STUDY_AUDIT_DIR / "phi_scrub_report.json"
        f.write_text("{}", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)

    def test_telemetry_rejected(self, monkeypatch_config: Path) -> None:
        """Telemetry lives under audit/ — off-limits."""
        import config

        f = config.TELEMETRY_SINK
        f.write_text("", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)

    def test_staging_rejected(self, monkeypatch_config: Path) -> None:
        import config

        config.STAGING_DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        f = config.STAGING_DATASETS_DIR / "leak.jsonl"
        f.write_text("{}", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)

    def test_arbitrary_filesystem_rejected(self, tmp_path: Path) -> None:
        """A path outside any configured zone must be rejected."""
        f = tmp_path / "etc" / "passwd"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("root:x:0:0", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)


class TestValidateAgentWrite:
    def test_agent_state_allowed(self, monkeypatch_config: Path) -> None:
        import config

        f = config.AGENT_OUTPUT_DIR / "new_output.csv"
        assert validate_agent_write(f) == Path(f.resolve())

    def test_agent_restore_points_allowed(self, monkeypatch_config: Path) -> None:
        """Operator restore-point tier (``output/{STUDY}/agent/restore_points/``)
        is agent-writable — the CLI tool needs to drop named runs there."""
        import config

        f = config.STUDY_RESTORE_POINTS_DIR / "run-x" / "snap.json"
        assert validate_agent_write(f) == Path(f.resolve())

    def test_tracked_snapshots_baseline_rejected(
        self, monkeypatch_config: Path
    ) -> None:
        """Tracked baseline at ``snapshots/{STUDY}/`` is OUTSIDE the agent
        write zone — only a maintainer (with shell access) curates it."""
        import config

        f = config.STUDY_SNAPSHOTS_DIR / "pdfs" / "evil.json"
        with pytest.raises(ZoneViolationError):
            validate_agent_write(f)

    def test_trio_bundle_rejected(self, monkeypatch_config: Path) -> None:
        """Agent may read trio bundle but must NOT write into it."""
        import config

        f = config.TRIO_DATASETS_DIR / "evil.jsonl"
        with pytest.raises(ZoneViolationError):
            validate_agent_write(f)

    def test_audit_rejected(self, monkeypatch_config: Path) -> None:
        import config

        f = config.STUDY_AUDIT_DIR / "tamper.json"
        with pytest.raises(ZoneViolationError):
            validate_agent_write(f)

    def test_allowlist_not_writable(self) -> None:
        """Repo-tracked YAML is read-allowlisted, not write-allowed."""
        project_root = Path(__file__).resolve().parents[1]
        yaml_path = project_root / "config" / "study_knowledge.yaml"
        with pytest.raises(ZoneViolationError):
            validate_agent_write(yaml_path)


class TestIsAgentReadable:
    def test_true_for_trio(self, monkeypatch_config: Path) -> None:
        import config

        assert is_agent_readable(config.TRIO_DATASETS_DIR)

    def test_true_for_agent(self, monkeypatch_config: Path) -> None:
        import config

        assert is_agent_readable(config.AGENT_OUTPUT_DIR)

    def test_false_for_audit(self, monkeypatch_config: Path) -> None:
        import config

        assert not is_agent_readable(config.STUDY_AUDIT_DIR)

    def test_false_for_arbitrary_path(self, tmp_path: Path) -> None:
        assert not is_agent_readable(tmp_path / "random")


class TestTraversalAndSymlinkSafety:
    """Realpath-based containment must neutralise symlinks and ``..`` traversal.

    The validator's entire security claim rests on ``os.path.realpath``
    normalising both. If these tests fail, an insider could plant a symlink
    inside an allowed zone that points at audit/ or staging/ and read it
    through the agent interface.
    """

    def test_symlink_from_agent_to_audit_rejected_on_read(
        self, monkeypatch_config: Path
    ) -> None:
        import config

        target = config.STUDY_AUDIT_DIR / "phi_scrub_report.json"
        target.write_text("{}", encoding="utf-8")
        link = config.AGENT_OUTPUT_DIR / "leak.json"
        link.symlink_to(target)
        with pytest.raises(ZoneViolationError):
            validate_agent_read(link)

    def test_symlink_from_agent_to_staging_rejected_on_read(
        self, monkeypatch_config: Path
    ) -> None:
        import config

        config.STAGING_DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        target = config.STAGING_DATASETS_DIR / "pre_scrub.jsonl"
        target.write_text("{}", encoding="utf-8")
        link = config.AGENT_OUTPUT_DIR / "leak.jsonl"
        link.symlink_to(target)
        with pytest.raises(ZoneViolationError):
            validate_agent_read(link)

    def test_symlink_from_agent_to_audit_rejected_on_write(
        self, monkeypatch_config: Path
    ) -> None:
        """A write through a symlink that resolves outside ``AGENT_STATE_DIR``
        must be blocked, even though the symlink itself lives inside."""
        import config

        target_dir = config.STUDY_AUDIT_DIR / "tamper_target"
        target_dir.mkdir(parents=True, exist_ok=True)
        link_dir = config.AGENT_OUTPUT_DIR / "tamper_link"
        link_dir.symlink_to(target_dir, target_is_directory=True)
        with pytest.raises(ZoneViolationError):
            validate_agent_write(link_dir / "tamper.json")

    def test_parent_traversal_escape_rejected_on_read(
        self, monkeypatch_config: Path
    ) -> None:
        """``agent/../audit/x.json`` must resolve out of the agent zone."""
        import config

        target = config.STUDY_AUDIT_DIR / "phi_scrub_report.json"
        target.write_text("{}", encoding="utf-8")
        traversal = config.AGENT_OUTPUT_DIR / ".." / ".." / "audit" / "phi_scrub_report.json"
        with pytest.raises(ZoneViolationError):
            validate_agent_read(traversal)

    def test_parent_traversal_escape_rejected_on_write(
        self, monkeypatch_config: Path
    ) -> None:
        import config

        traversal = (
            config.AGENT_OUTPUT_DIR / ".." / ".." / "audit" / "tamper.json"
        )
        with pytest.raises(ZoneViolationError):
            validate_agent_write(traversal)


class TestValidateSandboxWrite:
    """Exec-python sandbox is narrower than agent-tool write: only ``agent/analysis/``.

    The former ``str.startswith`` check admitted sibling prefixes like
    ``agent/analysis_exfil``; ``validate_sandbox_write`` uses commonpath.
    """

    def test_agent_output_allowed(self, monkeypatch_config: Path) -> None:
        import config

        f = config.AGENT_OUTPUT_DIR / "chart.csv"
        assert validate_sandbox_write(f) == Path(f.resolve())

    def test_sibling_prefix_rejected(self, monkeypatch_config: Path) -> None:
        """``agent/analysis_exfil/x.csv`` shares the string prefix
        ``agent/analysis`` but is NOT under ``agent/analysis/``.
        commonpath catches this; startswith did not."""
        import config

        sibling = config.AGENT_OUTPUT_DIR.parent / (
            config.AGENT_OUTPUT_DIR.name + "_exfil"
        )
        sibling.mkdir(parents=True, exist_ok=True)
        f = sibling / "x.csv"
        with pytest.raises(ZoneViolationError):
            validate_sandbox_write(f)

    def test_agent_state_outside_analysis_rejected(
        self, monkeypatch_config: Path
    ) -> None:
        """Sandbox write zone is narrower than agent-tool zone:
        other ``agent/`` subdirs like restore_points/ are rejected."""
        import config

        f = config.STUDY_RESTORE_POINTS_DIR / "tamper.json"
        with pytest.raises(ZoneViolationError):
            validate_sandbox_write(f)

    def test_symlink_to_audit_rejected(self, monkeypatch_config: Path) -> None:
        import config

        target_dir = config.STUDY_AUDIT_DIR / "exfil_target"
        target_dir.mkdir(parents=True, exist_ok=True)
        link_dir = config.AGENT_OUTPUT_DIR / "exfil_link"
        link_dir.symlink_to(target_dir, target_is_directory=True)
        with pytest.raises(ZoneViolationError):
            validate_sandbox_write(link_dir / "tamper.csv")
