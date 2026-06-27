import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

_INTAKE = (
    Path(__file__).resolve().parents[1]
    / "plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py"
)
_spec = importlib.util.spec_from_file_location("intake", _INTAKE)
intake = importlib.util.module_from_spec(_spec)
sys.modules["intake"] = intake
_spec.loader.exec_module(intake)

from scripts.audit.review_paths import intake_review_path  # noqa: E402

_RUN_PY = (
    Path(__file__).resolve().parents[1]
    / "plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py"
)


def test_classify_pdf_to_annotated():
    assert intake.classify("12A_FUA_annotated.PDF") == "annotated_pdfs"


def test_classify_dictionary_by_name_hint():
    assert intake.classify("Indo_VAP_DEB_mapping.xlsx") == "data_dictionary"
    assert intake.classify("study_codebook.csv") == "data_dictionary"
    assert intake.classify("DEB.xlsx") == "data_dictionary"


def test_classify_plain_dataset():
    assert intake.classify("14_Case_Control.xlsx") == "datasets"
    assert intake.classify("labs.csv") == "datasets"


def test_classify_unknown_extension_quarantines():
    assert intake.classify("readme.txt") == "_unclassified"
    assert intake.classify("notes.docx") == "_unclassified"
    assert intake.classify("nodotextension") == "_unclassified"


def _touch(path: Path, content: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_stage_flat_dir_copies_all(tmp_path):
    src = tmp_path / "delivery"
    _touch(src / "a.xlsx")
    _touch(src / "b.pdf")
    work = tmp_path / "work"
    staged = intake.stage_source(src, work)
    names = sorted(p.name for p in staged)
    assert names == ["a.xlsx", "b.pdf"]
    # source untouched
    assert (src / "a.xlsx").exists()


def test_stage_extracts_zip(tmp_path):
    src = tmp_path / "delivery"
    src.mkdir()
    payload = tmp_path / "payload.xlsx"
    _touch(payload, "data")
    zpath = src / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(payload, arcname="inside.xlsx")
    work = tmp_path / "work"
    staged = intake.stage_source(src, work)
    names = sorted(p.name for p in staged)
    assert names == ["inside.xlsx"]  # the .zip itself is not returned


def test_stage_single_file(tmp_path):
    f = tmp_path / "lone.csv"
    _touch(f)
    work = tmp_path / "work"
    staged = intake.stage_source(f, work)
    assert [p.name for p in staged] == ["lone.csv"]


def test_stage_missing_src_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        intake.stage_source(tmp_path / "nope", tmp_path / "work")


def test_intake_review_path():
    p = intake_review_path(Path("/out/STUDY/audit"))
    assert p == Path("/out/STUDY/audit/human_review/intake/intake_review.md")


def test_organize_sorts_and_drafts_manifest(tmp_path):
    src = tmp_path / "delivery"
    _touch(src / "14_Case_Control.xlsx")
    _touch(src / "labs.csv")
    _touch(src / "DEB_mapping.xlsx")
    _touch(src / "form12.pdf")
    _touch(src / "readme.txt")
    raw_root = tmp_path / "raw"
    cfg_root = tmp_path / "config"
    audit = tmp_path / "audit"

    res = intake.organize("STUDY", src, raw_root=raw_root, config_root=cfg_root, audit_dir=audit)

    base = raw_root / "STUDY"
    assert sorted(p.name for p in (base / "datasets").iterdir()) == [
        "14_Case_Control.xlsx",
        "labs.csv",
    ]
    assert [p.name for p in (base / "data_dictionary").iterdir()] == ["DEB_mapping.xlsx"]
    assert [p.name for p in (base / "annotated_pdfs").iterdir()] == ["form12.pdf"]
    assert [p.name for p in (base / "_unclassified").iterdir()] == ["readme.txt"]
    assert res.counts["datasets"] == 2
    assert res.unclassified == ["readme.txt"]
    # manifest drafted with both datasets as required
    manifest = (cfg_root / "STUDY" / "_forms_manifest.yaml").read_text()
    assert "DRAFT" in manifest
    assert "14_Case_Control.xlsx" in manifest and "labs.csv" in manifest
    assert res.manifest_written is True
    # review note written for the quarantined file
    assert res.review_note is not None
    note = (audit / "human_review" / "intake" / "intake_review.md").read_text()
    assert "readme.txt" in note
    assert res.skipped is False


def test_organize_noop_on_already_organized(tmp_path):
    raw_root = tmp_path / "raw"
    base = raw_root / "STUDY"
    for b in ("annotated_pdfs", "data_dictionary", "datasets", "_unclassified"):
        (base / b).mkdir(parents=True)
    _touch(base / "datasets" / "existing.xlsx")  # non-empty datasets => organized
    src = tmp_path / "delivery"
    _touch(src / "new.xlsx")

    res = intake.organize(
        "STUDY",
        src,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res.skipped is True
    # untouched: the new file was NOT copied in
    assert [p.name for p in (base / "datasets").iterdir()] == ["existing.xlsx"]


def test_organize_force_rebuilds(tmp_path):
    raw_root = tmp_path / "raw"
    base = raw_root / "STUDY"
    (base / "datasets").mkdir(parents=True)
    _touch(base / "datasets" / "existing.xlsx")
    src = tmp_path / "delivery"
    _touch(src / "new.xlsx")

    res = intake.organize(
        "STUDY",
        src,
        force=True,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res.skipped is False
    assert "new.xlsx" in [p.name for p in (base / "datasets").iterdir()]
    # force is additive-overwrite: pre-existing files are still present alongside new ones
    assert "existing.xlsx" in [p.name for p in (base / "datasets").iterdir()]


def test_organize_preserves_existing_manifest(tmp_path):
    src = tmp_path / "delivery"
    _touch(src / "a.xlsx")
    cfg_root = tmp_path / "config"
    mpath = cfg_root / "STUDY" / "_forms_manifest.yaml"
    mpath.parent.mkdir(parents=True)
    mpath.write_text("# hand-tuned\nrequired:\n  - a.xlsx\n")
    res = intake.organize(
        "STUDY",
        src,
        raw_root=tmp_path / "raw",
        config_root=cfg_root,
        audit_dir=tmp_path / "audit",
    )
    assert res.manifest_written is False
    assert "hand-tuned" in mpath.read_text()  # never clobbered


def test_run_py_emits_marker(tmp_path, monkeypatch):
    src = tmp_path / "delivery"
    _touch(src / "a.xlsx")
    _touch(src / "junk.txt")
    raw_root = tmp_path / "raw"
    monkeypatch.setenv("RPLN_INTAKE_RAW_ROOT", str(raw_root))
    monkeypatch.setenv("RPLN_INTAKE_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("RPLN_INTAKE_AUDIT_DIR", str(tmp_path / "audit"))
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(_RUN_PY), "--study", "STUDY", "--src", str(src)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("RPLN_SKILL_RESULT:")][-1]
    payload = json.loads(marker[len("RPLN_SKILL_RESULT:") :])
    assert payload["skill"] == "raw-data-intake"
    assert payload["ok"] is True
    assert payload["data"]["counts"]["datasets"] == 1
    assert payload["data"]["unclassified"] == ["junk.txt"]


def test_run_py_missing_src_fails(tmp_path):
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(_RUN_PY), "--study", "STUDY", "--src", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("RPLN_SKILL_RESULT:")][-1]
    payload = json.loads(marker[len("RPLN_SKILL_RESULT:") :])
    assert payload["ok"] is False


# --- Fix 1: name-collision tests ---


def test_stage_source_preserves_name_collision(tmp_path):
    """Two files with the same basename must both survive staging; no silent overwrite."""
    src = tmp_path / "delivery"
    _touch(src / "labs.csv", "top-level")
    _touch(src / "sub" / "labs.csv", "subdir")
    work = tmp_path / "work"
    collisions: list = []
    staged = intake.stage_source(src, work, collisions=collisions)
    # Both files must be present under distinct names.
    assert len(staged) == 2
    names = {p.name for p in staged}
    assert "labs.csv" in names
    # The colliding file got a disambiguated name.
    assert any(n.startswith("labs.collid") and n.endswith(".csv") for n in names)
    # Collision was recorded.
    assert "labs.csv" in collisions


def test_organize_collision_emits_review_note(tmp_path):
    """organize() must write a review note containing name_collision when a basename collision occurs."""
    src = tmp_path / "delivery"
    _touch(src / "labs.csv", "top")
    _touch(src / "sub" / "labs.csv", "sub")
    raw_root = tmp_path / "raw"
    cfg_root = tmp_path / "config"
    audit = tmp_path / "audit"

    res = intake.organize("STUDY", src, raw_root=raw_root, config_root=cfg_root, audit_dir=audit)

    assert res.review_note is not None
    note = (audit / "human_review" / "intake" / "intake_review.md").read_text()
    assert "name_collision" in note
    # Both staged files must exist in datasets/.
    dataset_files = list((raw_root / "STUDY" / "datasets").iterdir())
    assert len(dataset_files) == 2


# --- Fix 2: corrupt-zip emits structured failure marker ---


def test_run_py_corrupt_zip_emits_failure_marker(tmp_path):
    """A corrupt zip must not escape as an unstructured traceback; ok=False marker required."""
    src = tmp_path / "delivery"
    src.mkdir()
    (src / "bad.zip").write_bytes(b"not a zip")
    raw_root = tmp_path / "raw"
    env = {
        **__import__("os").environ,
        "RPLN_INTAKE_RAW_ROOT": str(raw_root),
        "RPLN_INTAKE_CONFIG_ROOT": str(tmp_path / "config"),
        "RPLN_INTAKE_AUDIT_DIR": str(tmp_path / "audit"),
    }
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(_RUN_PY), "--study", "STUDY", "--src", str(src)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0
    markers = [ln for ln in proc.stdout.splitlines() if ln.startswith("RPLN_SKILL_RESULT:")]
    assert markers, f"No RPLN_SKILL_RESULT marker in stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = json.loads(markers[-1][len("RPLN_SKILL_RESULT:") :])
    assert payload["ok"] is False


def test_organize_no_dictionary_is_still_idempotent(tmp_path):
    """A delivery with no data-dictionary file (the real Indo-VAP shape) must
    still pre-create the data_dictionary bucket so a re-run no-ops."""
    src = tmp_path / "delivery"
    _touch(src / "10_TST.xlsx")
    _touch(src / "form10.pdf")  # no dictionary-hint file at all
    raw_root = tmp_path / "raw"
    res = intake.organize(
        "STUDY",
        src,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res.skipped is False
    base = raw_root / "STUDY"
    # all four canonical buckets exist, even the empty ones
    for bucket in ("annotated_pdfs", "datasets", "data_dictionary", "_unclassified"):
        assert (base / bucket).is_dir(), f"missing bucket {bucket}"
    assert list((base / "data_dictionary").iterdir()) == []  # empty but present
    # second run must no-op despite the empty data_dictionary bucket
    res2 = intake.organize(
        "STUDY",
        src,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res2.skipped is True


def test_organize_add_files_into_organized_tree(tmp_path):
    """--add files NEW files into an already-organized study (no no-op skip)."""
    raw_root = tmp_path / "raw"
    base = raw_root / "STUDY"
    for b in ("annotated_pdfs", "data_dictionary", "datasets", "_unclassified"):
        (base / b).mkdir(parents=True)
    _touch(base / "datasets" / "existing.xlsx")  # organized: datasets non-empty
    src = tmp_path / "inbox"
    _touch(src / "new_form.xlsx")
    _touch(src / "new_scan.pdf")

    res = intake.organize(
        "STUDY",
        src,
        add=True,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res.skipped is False
    assert "new_form.xlsx" in [p.name for p in (base / "datasets").iterdir()]
    assert "new_scan.pdf" in [p.name for p in (base / "annotated_pdfs").iterdir()]
    assert "existing.xlsx" in [p.name for p in (base / "datasets").iterdir()]
    assert res.already_present == []


def test_organize_add_never_overwrites_existing(tmp_path):
    """--add records a same-named file as already_present and leaves it untouched."""
    raw_root = tmp_path / "raw"
    base = raw_root / "STUDY"
    (base / "datasets").mkdir(parents=True)
    existing = base / "datasets" / "form.xlsx"
    existing.write_text("ORIGINAL")  # pre-existing content
    src = tmp_path / "inbox"
    (src / "form.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (src / "form.xlsx").write_text("INCOMING")  # same name, different content

    res = intake.organize(
        "STUDY",
        src,
        add=True,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res.already_present == ["form.xlsx"]
    assert existing.read_text() == "ORIGINAL"  # never overwritten
    assert res.counts["datasets"] == 0  # nothing newly placed


def test_stage_source_skips_managed_dirs_and_junk(tmp_path):
    """Walking a dir skips snapshots/, hidden/junk, and anything under exclude_under."""
    root = tmp_path / "data"
    _touch(root / "loose.xlsx")  # loose in data/  -> staged
    _touch(root / "sub" / "nested.csv")  # legit subfolder -> staged
    _touch(root / "snapshots" / "snap.jsonl")  # managed         -> skipped
    _touch(root / ".DS_Store")  # junk            -> skipped
    _touch(root / "raw" / "STUDY" / "datasets" / "x.xlsx")  # dest tree -> skipped
    work = tmp_path / "work"
    staged = intake.stage_source(root, work, exclude_under=[root / "raw"])
    assert sorted(p.name for p in staged) == ["loose.xlsx", "nested.csv"]


def test_organize_src_is_data_dir_ignores_own_raw_tree(tmp_path):
    """SRC=data with the study's own raw/ underneath: only loose files are filed."""
    data = tmp_path / "data"
    raw_root = data / "raw"
    base = raw_root / "STUDY"
    # pre-existing organized tree under data/raw
    for b in ("annotated_pdfs", "data_dictionary", "datasets", "_unclassified"):
        (base / b).mkdir(parents=True)
    _touch(base / "datasets" / "old.xlsx")
    _touch(data / "snapshots" / "snap.jsonl")  # must be ignored
    # new loose files dropped directly into data/
    _touch(data / "new_form.xlsx")
    _touch(data / "scan.pdf")

    res = intake.organize(
        "STUDY",
        data,
        add=True,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    ds = sorted(p.name for p in (base / "datasets").iterdir())
    assert ds == ["new_form.xlsx", "old.xlsx"]  # old not re-ingested/duplicated
    assert [p.name for p in (base / "annotated_pdfs").iterdir()] == ["scan.pdf"]
    # the snapshot jsonl was never pulled in as a dataset/unclassified
    assert "snap.jsonl" not in [p.name for p in (base / "_unclassified").iterdir()]


def test_prune_source_deletes_staged_files_only(tmp_path):
    """prune_source removes filed files but never anything under exclude_under."""
    data = tmp_path / "data"
    raw_root = data / "raw"
    _touch(raw_root / "STUDY" / "datasets" / "keep.xlsx")  # dest tree, excluded
    _touch(data / "loose.xlsx")
    _touch(data / "sub" / "nested.pdf")
    pruned = intake.prune_source(data, exclude_under=[raw_root])
    assert sorted(pruned) == ["loose.xlsx", "nested.pdf"]
    assert not (data / "loose.xlsx").exists()
    assert not (data / "sub").exists()  # emptied subdir removed
    assert (raw_root / "STUDY" / "datasets" / "keep.xlsx").exists()  # dest untouched


def test_organize_prune_files_data_then_cleans_source(tmp_path):
    """End-to-end: SRC=data, file loose new files into raw, then prune the source."""
    data = tmp_path / "data"
    raw_root = data / "raw"
    base = raw_root / "STUDY"
    for b in ("annotated_pdfs", "data_dictionary", "datasets", "_unclassified"):
        (base / b).mkdir(parents=True)
    _touch(base / "datasets" / "old.xlsx")  # organized tree
    _touch(data / "new_form.xlsx")  # loose new files
    _touch(data / "scan.pdf")

    res = intake.organize(
        "STUDY",
        data,
        add=True,
        prune=True,
        raw_root=raw_root,
        config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    # filed into the raw tree
    assert "new_form.xlsx" in [p.name for p in (base / "datasets").iterdir()]
    assert "scan.pdf" in [p.name for p in (base / "annotated_pdfs").iterdir()]
    # source loose copies removed
    assert sorted(res.pruned) == ["new_form.xlsx", "scan.pdf"]
    assert not (data / "new_form.xlsx").exists()
    assert not (data / "scan.pdf").exists()
    # the study's own raw tree was never pruned
    assert (base / "datasets" / "old.xlsx").exists()


def test_resolve_study_name_explicit_valid():
    assert intake.resolve_study_name("Cohort-7") == ("Cohort-7", "explicit")
    assert intake.resolve_study_name("  Trimmed  ") == ("Trimmed", "explicit")


def test_resolve_study_name_rejects_path_injection():
    import pytest

    for bad in ("a/b", "..", ".", "x\\y"):
        with pytest.raises(ValueError):
            intake.resolve_study_name(bad)


def test_resolve_study_name_detects_existing_study(tmp_path):
    raw = tmp_path / "raw"
    (raw / "Cohort-9" / "datasets").mkdir(parents=True)
    name, source = intake.resolve_study_name(None, raw_root=raw, env_study_name="")
    assert (name, source) == ("Cohort-9", "detected")


def test_resolve_study_name_refuses_when_nothing_detected(tmp_path):
    import pytest

    raw = tmp_path / "raw"
    raw.mkdir()  # no study with a datasets/ dir
    with pytest.raises(ValueError):
        intake.resolve_study_name(None, raw_root=raw, env_study_name="")


def test_resolve_study_name_env_acts_as_explicit(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()  # nothing detectable, but env names it
    name, source = intake.resolve_study_name(None, raw_root=raw, env_study_name="EnvStudy")
    assert (name, source) == ("EnvStudy", "explicit")
