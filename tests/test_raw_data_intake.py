import importlib.util
from pathlib import Path

_INTAKE = (
    Path(__file__).resolve().parents[1]
    / "plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py"
)
_spec = importlib.util.spec_from_file_location("intake", _INTAKE)
intake = importlib.util.module_from_spec(_spec)
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
