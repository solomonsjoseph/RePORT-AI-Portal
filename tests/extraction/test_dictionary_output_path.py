# tests/extraction/test_dictionary_output_path.py
import config


def test_dictionary_output_path_under_llm_source():
    assert "llm_source" in str(config.DICTIONARY_OUTPUT_PATH)
    assert "trio_bundle" not in str(config.DICTIONARY_OUTPUT_PATH)
