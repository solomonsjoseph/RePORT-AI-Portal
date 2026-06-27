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
