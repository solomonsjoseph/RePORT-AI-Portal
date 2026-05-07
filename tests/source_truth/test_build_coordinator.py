# tests/source_truth/test_build_coordinator.py
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.source_truth.build import BuildCoordinatorError, run_build
from scripts.source_truth.policy_loader import DuplicateFormNameError


def test_run_build_resolves_paths_and_creates_output_dirs(tmp_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )
    assert (output_root / "llm_source").is_dir()
    assert (output_root / "llm_source" / "evidence_packs").is_dir()
    assert (output_root / "llm_source" / "concept").is_dir()
    assert (output_root / "audit").is_dir()
    assert (output_root / "staging" / "llm_source").is_dir()


def test_run_build_blocks_on_missing_policies_dir(tmp_path):
    with pytest.raises(BuildCoordinatorError, match="policies_dir"):
        run_build(
            study="Mini",
            policies_dir=tmp_path / "does_not_exist",
            output_root=tmp_path / "output",
            column_inventory=None,
        )


def test_cli_module_invocable(tmp_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python", "-m", "scripts.source_truth.build",
            "--study", "Mini",
            "--policies-dir", str(fixture / "data" / "Mini" / "SoT"),
            "--output-root", str(tmp_path / "cli_run"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI failed: stdout={result.stdout!r} stderr={result.stderr!r}"


def test_run_build_emits_catalog_and_evidence_packs(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )

    catalog_path = output_root / "llm_source" / "study_metadata_catalog.json"
    assert catalog_path.is_file()
    catalog = json.loads(catalog_path.read_text())
    assert catalog["artifact_type"] == "study_metadata_catalog"
    assert isinstance(catalog["compact_records"], list)
    assert len(catalog["compact_records"]) > 0

    evidence_dir = output_root / "llm_source" / "evidence_packs"
    pack_files = list(evidence_dir.glob("*.json"))
    assert len(pack_files) > 0
    sample = json.loads(pack_files[0].read_text())
    assert sample["variable_id"] == pack_files[0].stem


def test_run_build_idempotent_byte_identical(tmp_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    for out in (out_a, out_b):
        run_build(
            study="Mini",
            policies_dir=fixture / "data" / "Mini" / "SoT",
            output_root=out,
            column_inventory=None,
        )

    cat_a = (out_a / "llm_source" / "study_metadata_catalog.json").read_bytes()
    cat_b = (out_b / "llm_source" / "study_metadata_catalog.json").read_bytes()
    assert cat_a == cat_b


def test_run_build_emits_declared_ledgers(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )

    phi_path = output_root / "audit" / "phi_handling_ledger.declared.json"
    cleanup_path = output_root / "audit" / "dataset_cleanup_ledger.declared.json"
    assert phi_path.is_file()
    assert cleanup_path.is_file()
    phi = json.loads(phi_path.read_text())
    cleanup = json.loads(cleanup_path.read_text())
    assert phi["artifact_type"] == "phi_handling_ledger"
    assert cleanup["artifact_type"] == "dataset_cleanup_ledger"
    assert isinstance(phi.get("entries"), list)
    assert isinstance(cleanup.get("entries"), list)


def test_run_build_emits_initial_concept_index(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )

    index_path = output_root / "llm_source" / "concept" / "concept_index.json"
    assert index_path.is_file()
    index = json.loads(index_path.read_text())
    assert index["artifact_type"] == "study_concept_index"
    assert index["policy_status"] == "derived_from_sot"
    assert "cohort_a" in index["cohorts"]
    members = index["cohorts"]["cohort_a"]["member_variables"]
    for member in members:
        assert member["analysis_queryable"] is None


def test_run_build_stage2_emits_schema_and_enriched_concept_index_to_staging(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=fixture / "data" / "Mini" / "column_inventory.json",
    )

    schema_path = output_root / "staging" / "llm_source" / "phi_handled_dataset_schema.json"
    assert schema_path.is_file()
    schema = json.loads(schema_path.read_text())
    assert schema["artifact_type"] == "study_dataset_schema"
    assert isinstance(schema["entries"], list)

    enriched_path = output_root / "staging" / "llm_source" / "concept" / "concept_index.json"
    assert enriched_path.is_file()
    enriched = json.loads(enriched_path.read_text())
    members = enriched["cohorts"]["cohort_a"]["member_variables"]
    for member in members:
        assert member["analysis_queryable"] in (True, False)


def test_compact_records_have_form_field_populated(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )
    catalog = json.loads((output_root / "llm_source" / "study_metadata_catalog.json").read_text())
    expected_forms = {"19_Smear", "1A_ICScreening", "2A_ICBaseline"}
    for record in catalog["compact_records"]:
        assert record.get("form") in expected_forms, (
            f"variable {record['variable_id']} has form={record.get('form')!r}; "
            f"expected one of {expected_forms}"
        )


def test_run_build_stage2_dataset_schema_reflects_column_inventory(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=fixture / "data" / "Mini" / "column_inventory.json",
    )
    schema = json.loads(
        (output_root / "staging" / "llm_source" / "phi_handled_dataset_schema.json").read_text()
    )
    entries_by_form: dict[str, list[str]] = {}
    for entry in schema["entries"]:
        form = entry.get("form")
        if form is None:
            # Per-entry form may be derived from elsewhere — accept absence
            continue
        entries_by_form.setdefault(form, []).append(entry.get("variable_id"))

    # Sanity: at least one entry per form in the inventory
    inventory = json.loads(
        (fixture / "data" / "Mini" / "column_inventory.json").read_text()
    )["forms"]
    for form, body in inventory.items():
        for col in body["columns"]:
            # Each inventory column should produce at least one schema entry
            # whose variable_id matches (whether or not 'form' is on the entry,
            # the variable_id presence is enforceable).
            schema_vids = {e.get("variable_id") for e in schema["entries"]}
            assert col in schema_vids, (
                f"column {col!r} in inventory for form {form!r} not present in schema entries"
            )


GOLDEN_DIR = Path("tests/fixtures/build_mini/expected_outputs")


def test_run_build_rejects_duplicate_form_names(tmp_path):
    """Two policy YAMLs declaring the same ``form:`` value would silently
    clobber each other in the aggregated catalog, ledgers, and concept index.
    The build coordinator must raise before any artifacts are emitted."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()

    policy_a = {
        "schema_version": 2,
        "study": "Mini",
        "form": "form_dup",
        "source": {"dataset_file": "form_dup_a.xlsx"},
        "variables": {"A": {"record_type": "variable"}},
    }
    policy_b = {
        "schema_version": 2,
        "study": "Mini",
        "form": "form_dup",
        "source": {"dataset_file": "form_dup_b.xlsx"},
        "variables": {"B": {"record_type": "variable"}},
    }
    (sot_dir / "form_dup_a_policy.yaml").write_text(
        yaml.safe_dump(policy_a), encoding="utf-8"
    )
    (sot_dir / "form_dup_b_policy.yaml").write_text(
        yaml.safe_dump(policy_b), encoding="utf-8"
    )

    output_root = tmp_path / "output" / "Mini"

    with pytest.raises(DuplicateFormNameError, match="form_dup"):
        run_build(
            study="Mini",
            policies_dir=sot_dir,
            output_root=output_root,
            column_inventory=None,
        )

    # Hard invariant: NO artifacts emitted when duplicate detection trips.
    assert not (output_root / "llm_source" / "study_metadata_catalog.json").exists()
    assert not (output_root / "audit" / "phi_handling_ledger.declared.json").exists()
    assert not (
        output_root / "llm_source" / "concept" / "concept_index.json"
    ).exists()


def test_build_cli_returns_2_on_duplicate_form_names(tmp_path):
    """The CLI ``main()`` must convert the duplicate-form ``ValueError`` into
    a clean exit code 2 — not a Python stack trace."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    for letter, var in (("a", "A"), ("b", "B")):
        policy = {
            "schema_version": 2,
            "study": "Mini",
            "form": "form_dup",
            "source": {"dataset_file": f"form_dup_{letter}.xlsx"},
            "variables": {var: {"record_type": "variable"}},
        }
        (sot_dir / f"form_dup_{letter}_policy.yaml").write_text(
            yaml.safe_dump(policy), encoding="utf-8"
        )

    result = subprocess.run(
        [
            "uv",
            "run",
            "--all-groups",
            "python",
            "-m",
            "scripts.source_truth.build",
            "--study",
            "Mini",
            "--policies-dir",
            str(sot_dir),
            "--output-root",
            str(tmp_path / "cli_run"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "duplicate form name" in result.stderr.lower()
    assert "form_dup" in result.stderr


def _make_minimal_policy(form: str, variables: dict[str, dict]) -> dict:
    """Build a minimal policy artifact with compact-target variables.

    Each entry in ``variables`` declares a ``variable_id`` and the policy
    fields required by ``catalog.build_catalog_artifact`` to emit a compact
    record (presence.dataset.present=True, normalized.analysis_queryable=True,
    derivation_targets=['catalog']). The intent is to exercise
    ``_aggregate_catalog`` on a tiny in-memory fixture without writing
    YAMLs to disk.
    """
    records = []
    for vid in variables:
        records.append(
            {
                "variable_id": vid,
                "source_kind": "form_field",
                "review_state": "approved",
                "derivation_targets": ["catalog"],
                "presence": {
                    "dataset": {"present": True, "column": vid},
                    "pdf": {"present": False},
                    "dictionary": {"present": False},
                },
                "normalized": {
                    "label": vid,
                    "handling_action": "retain",
                    "analysis_queryable": True,
                },
                "exact_source_wording": {},
                "source_references": {},
            }
        )
    return {
        "artifact_type": "source_truth_policy",
        "study": "Synth",
        "form": form,
        "source_file": f"{form}_policy.yaml",
        "records": records,
    }


def test_aggregate_catalog_tracks_seen_in_forms_two_form_duplicate():
    """Cross-form duplicate gains seen_in_forms with both forms; first-form-wins
    keeps the canonical record content from the first form encountered."""
    from scripts.source_truth.build import _aggregate_catalog

    artifacts = [
        _make_minimal_policy("FORM_A", {"SUBJID": {}, "ONLY_A": {}}),
        _make_minimal_policy("FORM_B", {"SUBJID": {}, "ONLY_B": {}}),
    ]
    aggregated = _aggregate_catalog(artifacts)
    by_vid = {r["variable_id"]: r for r in aggregated["compact_records"]}
    # First-form-wins: SUBJID appears once.
    assert sorted(by_vid) == ["ONLY_A", "ONLY_B", "SUBJID"]
    # Canonical form is the first one encountered.
    assert by_vid["SUBJID"]["form"] == "FORM_A"
    # Cross-form duplicate: seen_in_forms lists both, sorted.
    assert by_vid["SUBJID"]["seen_in_forms"] == ["FORM_A", "FORM_B"]
    # Single-form variables list exactly one form each.
    assert by_vid["ONLY_A"]["seen_in_forms"] == ["FORM_A"]
    assert by_vid["ONLY_B"]["seen_in_forms"] == ["FORM_B"]


def test_aggregate_catalog_tracks_seen_in_forms_three_form_duplicate():
    """When a vid appears in three forms, seen_in_forms has all three sorted."""
    from scripts.source_truth.build import _aggregate_catalog

    artifacts = [
        _make_minimal_policy("FORM_C", {"SUBJID": {}}),
        _make_minimal_policy("FORM_A", {"SUBJID": {}}),
        _make_minimal_policy("FORM_B", {"SUBJID": {}}),
    ]
    aggregated = _aggregate_catalog(artifacts)
    [record] = aggregated["compact_records"]
    # First-form-wins: canonical form is FORM_C (first artifact in list).
    assert record["form"] == "FORM_C"
    # seen_in_forms is sorted alphabetically, NOT iteration order.
    assert record["seen_in_forms"] == ["FORM_A", "FORM_B", "FORM_C"]


def test_aggregate_catalog_tracks_seen_in_forms_on_evidence_packs():
    """Evidence packs gain the same seen_in_forms metadata."""
    from scripts.source_truth.build import _aggregate_catalog

    artifacts = [
        _make_minimal_policy("FORM_A", {"SUBJID": {}, "ONLY_A": {}}),
        _make_minimal_policy("FORM_B", {"SUBJID": {}, "ONLY_B": {}}),
    ]
    aggregated = _aggregate_catalog(artifacts)
    packs = {p["variable_id"]: p for p in aggregated["evidence_packs"]}
    assert sorted(packs) == ["ONLY_A", "ONLY_B", "SUBJID"]
    assert packs["SUBJID"]["seen_in_forms"] == ["FORM_A", "FORM_B"]
    assert packs["ONLY_A"]["seen_in_forms"] == ["FORM_A"]
    assert packs["ONLY_B"]["seen_in_forms"] == ["FORM_B"]


def test_aggregate_catalog_first_form_wins_record_content_unchanged():
    """The canonical record's content (other than seen_in_forms) is identical
    to what would be emitted if only the first form were processed."""
    from scripts.source_truth.build import _aggregate_catalog

    artifacts_dup = [
        _make_minimal_policy("FORM_A", {"SUBJID": {}}),
        _make_minimal_policy("FORM_B", {"SUBJID": {}}),
    ]
    artifacts_solo = [_make_minimal_policy("FORM_A", {"SUBJID": {}})]
    dup = _aggregate_catalog(artifacts_dup)["compact_records"][0]
    solo = _aggregate_catalog(artifacts_solo)["compact_records"][0]
    dup_no_seen = {k: v for k, v in dup.items() if k != "seen_in_forms"}
    solo_no_seen = {k: v for k, v in solo.items() if k != "seen_in_forms"}
    assert dup_no_seen == solo_no_seen


def test_run_build_mini_seen_in_forms_subjid_three_forms(tmp_path):
    """Real Mini fixture smoke: SUBJID appears in all three Mini forms."""
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )
    catalog = json.loads(
        (output_root / "llm_source" / "study_metadata_catalog.json").read_text()
    )
    by_vid = {r["variable_id"]: r for r in catalog["compact_records"]}
    assert "SUBJID" in by_vid, "SUBJID expected in Mini catalog"
    assert by_vid["SUBJID"]["seen_in_forms"] == [
        "19_Smear",
        "1A_ICScreening",
        "2A_ICBaseline",
    ]
    # Every compact record carries the field, sorted, with canonical form in it.
    for r in catalog["compact_records"]:
        assert "seen_in_forms" in r
        assert isinstance(r["seen_in_forms"], list)
        assert r["seen_in_forms"] == sorted(r["seen_in_forms"])
        assert r["form"] in r["seen_in_forms"]


def test_run_build_mini_seen_in_forms_on_evidence_packs(tmp_path):
    """Evidence packs emitted by the build also carry seen_in_forms."""
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=None,
    )
    pack_path = output_root / "llm_source" / "evidence_packs" / "SUBJID.json"
    assert pack_path.is_file()
    pack = json.loads(pack_path.read_text())
    assert pack["seen_in_forms"] == [
        "19_Smear",
        "1A_ICScreening",
        "2A_ICBaseline",
    ]


@pytest.mark.parametrize(
    "rel_path",
    [
        "llm_source/study_metadata_catalog.json",
        "llm_source/concept/concept_index.json",
        "audit/phi_handling_ledger.declared.json",
        "audit/dataset_cleanup_ledger.declared.json",
    ],
)
def test_run_build_byte_identical_to_golden(tmp_path, rel_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini" / "SoT",
        output_root=output_root,
        column_inventory=fixture / "data" / "Mini" / "column_inventory.json",
    )
    actual = (output_root / rel_path).read_bytes()
    golden = (GOLDEN_DIR / rel_path).read_bytes()
    assert actual == golden, f"build output for {rel_path} differs from golden"
