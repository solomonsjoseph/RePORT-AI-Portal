"""Anti-drift contract test for skill invocation strategies (B1).

Documents and verifies that each skill is invoked via its configured strategy:
- `subprocess`: via `invoke_skill()` with RPLN_SKILL_RESULT marker
- `in_process_supervised`: called directly within publish supervisor (locked atomic)
- `native_module`: imported as a library; not called via orchestrator DAG

This test prevents unintentional drift where a skill's invocation method changes
without updating plugin.yaml, SKILL.md, or related tests. The deterministic
suite baseline (B1 acceptance criterion) must pass with zero invocation changes.
"""

from __future__ import annotations

from pathlib import Path

# Map of skill name → expected invocation strategy from plugin.yaml
EXPECTED_INVOCATION: dict[str, str] = {
    # Subprocess skills: invoked via invoke_skill() with RPLN_SKILL_RESULT marker
    "header-extraction": "subprocess",
    "dictionary-to-llm-source": "subprocess",
    "dataset-deduplication": "subprocess",
    "sot-lean-generator": "subprocess",  # CLI tool; also has direct import in P1b
    "dataset-to-llm-source": "subprocess",  # publish supervisor
    "audit-verification": "subprocess",
    "excel-duplicate-handler": "native_module",  # legacy maintainer CLI, not a pipeline DAG node
    "study-setup": "subprocess",  # interactive
    # In-process supervised: called within publish supervisor under lock
    "phi-classification": "in_process_supervised",  # Phase 3, 3b (cross-form barrier)
    "phi-scrubbing": "in_process_supervised",  # Phase 4
    # Native module: imported as library; no DAG invocation
    "phi-rulebook": "native_module",
}

# P1b SoT generation is a special case: invoked via direct import from
# scripts.source_truth.generate_lean_outputs, NOT via invoke_skill().
# See SKILL_INVOCATION.md for details.
DIRECT_IMPORT_SKILLS: dict[str, str] = {
    "sot-lean-generator": "scripts.source_truth.generate_lean_outputs",
}


class TestInvocationContract:
    """Verify skill invocation strategies match configuration and code."""

    def test_plugin_yaml_has_invocation_fields(self) -> None:
        """Every skill entry in plugin.yaml must have an invocation: field."""

        import yaml

        plugin_yaml_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "plugin.yaml"
        )
        assert plugin_yaml_path.is_file(), f"plugin.yaml not found: {plugin_yaml_path}"

        data = yaml.safe_load(plugin_yaml_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        skills_section = data.get("skills", [])
        assert isinstance(skills_section, list)

        found_skills: dict[str, str] = {}
        for skill_entry in skills_section:
            if not isinstance(skill_entry, dict):
                continue
            name = skill_entry.get("skill")
            if not name:
                continue
            invocation = skill_entry.get("invocation")
            assert invocation, f"Skill {name} missing invocation field"
            assert invocation in (
                "subprocess",
                "in_process_supervised",
                "native_module",
            ), f"Skill {name} has invalid invocation value: {invocation}"
            found_skills[name] = invocation

        # Verify all expected skills are present with correct invocation
        for skill_name, expected_invocation in EXPECTED_INVOCATION.items():
            assert skill_name in found_skills, f"Skill {skill_name} not found in plugin.yaml"
            actual = found_skills[skill_name]
            assert actual == expected_invocation, (
                f"Skill {skill_name} invocation mismatch: expected {expected_invocation}, got {actual}"
            )

    def test_subprocess_skills_have_run_entry(self) -> None:
        """All subprocess skills must have a run: field pointing to scripts/run.py."""

        import yaml

        plugin_yaml_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "plugin.yaml"
        )
        data = yaml.safe_load(plugin_yaml_path.read_text(encoding="utf-8"))
        skills_section = data.get("skills", [])

        for skill_entry in skills_section:
            if not isinstance(skill_entry, dict):
                continue
            name = skill_entry.get("skill")
            invocation = skill_entry.get("invocation")
            if invocation != "subprocess":
                continue
            # Subprocess skills must have a run entry
            run_path = skill_entry.get("run")
            assert run_path, f"Subprocess skill {name} missing run field"
            assert "run.py" in run_path, (
                f"Subprocess skill {name} run path doesn't point to run.py: {run_path}"
            )

    def test_in_process_supervised_skills_invoked_in_publish(self) -> None:
        """Verify phi-classification and phi-scrubbing are called in-process by publish."""

        extract_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "skills"
            / "dataset-to-llm-source"
            / "scripts"
            / "extract_to_llm_source.py"
        )
        assert extract_path.is_file()

        code = extract_path.read_text(encoding="utf-8")

        # phi-classification is invoked via review_form_headers (imported from phi_review)
        assert "from scripts.security.phi_review import" in code, (
            "phi-classification (via phi_review) not imported in extract_to_llm_source"
        )
        assert "review_form_headers" in code, (
            "review_form_headers not called in extract_to_llm_source"
        )

        # Cross-form consistency barrier (P3b in-process)
        assert "_apply_cross_form_consistency" in code, (
            "Cross-form consistency barrier not found in extract_to_llm_source"
        )

        # phi-scrubbing is invoked via run_phi_scrub in host_pipeline
        host_pipeline_path = (
            Path(__file__).resolve().parents[2] / "scripts" / "pipeline" / "host_pipeline.py"
        )
        assert host_pipeline_path.is_file()
        pipeline_code = host_pipeline_path.read_text(encoding="utf-8")
        assert (
            "run_phi_scrub" in pipeline_code or "from scripts.security.phi_scrub" in pipeline_code
        ), "phi-scrubbing (via run_phi_scrub) not imported in host_pipeline"

    def test_direct_import_sot_generation_p1b(self) -> None:
        """Verify P1b SoT generation uses direct import, not invoke_skill."""

        run_py_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "skills"
            / "report-ai-study-pipeline"
            / "scripts"
            / "run.py"
        )
        assert run_py_path.is_file()

        code = run_py_path.read_text(encoding="utf-8")

        # P1b: direct import of generate_lean_outputs.main
        assert "from scripts.source_truth.generate_lean_outputs import main" in code, (
            "P1b does not import generate_lean_outputs.main for direct invocation"
        )
        # The direct call must actually be made (not just imported). The real
        # P1b leg imports `main as generate_lean_outputs_main` and calls it; we
        # assert the call site directly rather than windowing on a section header
        # (the literal "P1b SoT" also appears in the module docstring).
        assert "generate_lean_outputs_main(" in code, (
            "P1b does not call generate_lean_outputs_main(...) directly"
        )

    def test_native_module_skills_not_in_dag_invocation(self) -> None:
        """Native module skills (phi-rulebook) are never called via DAG invoke_skill."""

        run_py_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "skills"
            / "report-ai-study-pipeline"
            / "scripts"
            / "run.py"
        )
        assert run_py_path.is_file()

        code = run_py_path.read_text(encoding="utf-8")

        # phi-rulebook should be imported as a native module, never via invoke_skill
        # (it is consumed by preflight and phi-classification, not orchestrated)
        assert 'invoke_skill("phi-rulebook"' not in code, (
            "phi-rulebook should not be invoked via invoke_skill in orchestrator"
        )

    def test_skill_md_invocation_note_exists(self) -> None:
        """P1b SoT generation SKILL.md documents direct import invocation."""

        skill_invocation_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "skills"
            / "sot-lean-generator"
            / "SKILL_INVOCATION.md"
        )
        assert skill_invocation_path.is_file(), (
            f"P1b invocation documentation not found: {skill_invocation_path}"
        )

        content = skill_invocation_path.read_text(encoding="utf-8")
        assert "Direct Import" in content or "direct" in content.lower(), (
            "SKILL_INVOCATION.md doesn't mention direct import"
        )
        assert "generate_lean_outputs" in content, (
            "SKILL_INVOCATION.md doesn't mention generate_lean_outputs"
        )
        assert "Anti-Drift" in content or "anti-drift" in content.lower(), (
            "SKILL_INVOCATION.md doesn't include anti-drift notes"
        )


class TestInvocationStability:
    """Verify invocation method does not drift across suite runs."""

    def test_invocation_method_immutable(self) -> None:
        """Sanity check: EXPECTED_INVOCATION map matches reality.

        This test is a no-op production check; it documents the invariant
        that the invocation contract is stable. If this fails, it means a
        skill's invocation method changed without updating the test.
        """
        # Load plugin.yaml and verify invocation fields match expectations

        import yaml

        plugin_yaml_path = (
            Path(__file__).resolve().parents[2]
            / "plugins"
            / "report-ai-study-pipeline"
            / "plugin.yaml"
        )
        data = yaml.safe_load(plugin_yaml_path.read_text(encoding="utf-8"))
        skills_section = data.get("skills", [])

        actual_invocation: dict[str, str] = {}
        for skill_entry in skills_section:
            if not isinstance(skill_entry, dict):
                continue
            name = skill_entry.get("skill")
            invocation = skill_entry.get("invocation")
            if name and invocation:
                actual_invocation[name] = invocation

        # Every expected skill must have the expected invocation method
        for name, expected in EXPECTED_INVOCATION.items():
            actual = actual_invocation.get(name)
            assert actual == expected, (
                f"Invocation drift detected for {name}: "
                f"expected {expected}, got {actual}. "
                f"Update plugin.yaml, SKILL.md, and this test accordingly."
            )
