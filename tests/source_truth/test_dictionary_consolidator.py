# tests/source_truth/test_dictionary_consolidator.py
import json
from pathlib import Path

import pytest

from scripts.source_truth.dictionary_consolidator import (
    DictionaryConsolidatorError,
    consolidate_dictionary,
)


def test_consolidate_empty_dir_returns_empty_artifact(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "data_dictionary.json"

    result = consolidate_dictionary(study="Mini", source_dir=src, output_path=dst)

    assert dst.is_file()
    artifact = json.loads(dst.read_text())
    assert artifact["artifact_type"] == "study_data_dictionary"
    assert artifact["study"] == "Mini"
    assert artifact["source"] == "dictionary_workbook"
    assert artifact["tables"] == {}
    assert result["files_consolidated"] == 0


def test_consolidate_merges_multiple_json_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "labels.json").write_text(json.dumps({"a": 1}))
    (src / "codes.json").write_text(json.dumps([{"code": "X"}]))
    dst = tmp_path / "data_dictionary.json"

    consolidate_dictionary(study="Mini", source_dir=src, output_path=dst)

    artifact = json.loads(dst.read_text())
    assert artifact["tables"]["labels"] == {"a": 1}
    assert artifact["tables"]["codes"] == [{"code": "X"}]


def test_consolidate_missing_source_dir_raises(tmp_path):
    dst = tmp_path / "data_dictionary.json"
    with pytest.raises(DictionaryConsolidatorError, match="source_dir"):
        consolidate_dictionary(
            study="Mini", source_dir=tmp_path / "no_such_dir", output_path=dst
        )


def test_consolidate_byte_identical_repeat_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.json").write_text(json.dumps({"k": "v"}, sort_keys=True))
    (src / "b.json").write_text(json.dumps({"j": "u"}, sort_keys=True))
    dst1 = tmp_path / "first.json"
    dst2 = tmp_path / "second.json"

    consolidate_dictionary(study="Mini", source_dir=src, output_path=dst1)
    consolidate_dictionary(study="Mini", source_dir=src, output_path=dst2)
    assert dst1.read_bytes() == dst2.read_bytes()
