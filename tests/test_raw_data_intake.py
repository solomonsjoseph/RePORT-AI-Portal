import importlib.util
import sys
from pathlib import Path

_INTAKE = (
    Path(__file__).resolve().parents[1]
    / "plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py"
)
_spec = importlib.util.spec_from_file_location("intake", _INTAKE)
intake = importlib.util.module_from_spec(_spec)
sys.modules["intake"] = intake
_spec.loader.exec_module(intake)


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


import zipfile


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


from pathlib import Path as _P
from scripts.audit.review_paths import intake_review_path


def test_intake_review_path():
    p = intake_review_path(_P("/out/STUDY/audit"))
    assert p == _P("/out/STUDY/audit/human_review/intake/intake_review.md")


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

    res = intake.organize(
        "STUDY", src, raw_root=raw_root, config_root=cfg_root, audit_dir=audit
    )

    base = raw_root / "STUDY"
    assert sorted(p.name for p in (base / "datasets").iterdir()) == ["14_Case_Control.xlsx", "labs.csv"]
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
        "STUDY", src, raw_root=raw_root, config_root=tmp_path / "config",
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
        "STUDY", src, force=True, raw_root=raw_root,
        config_root=tmp_path / "config", audit_dir=tmp_path / "audit",
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
        "STUDY", src, raw_root=tmp_path / "raw", config_root=cfg_root,
        audit_dir=tmp_path / "audit",
    )
    assert res.manifest_written is False
    assert "hand-tuned" in mpath.read_text()  # never clobbered
