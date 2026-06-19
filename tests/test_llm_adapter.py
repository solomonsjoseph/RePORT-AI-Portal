"""Tests for the shared LLM JSON adapter (Notes 7 + 9 foundation)."""

from __future__ import annotations

import pytest

from scripts.ai_assistant.llm_adapter import (
    PromptValueLeakError,
    extract_json,
    guard_prompt_value_free,
)


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_strips_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('```\n{"b": 2}\n```') == {"b": 2}


def test_extract_json_finds_object_in_prose():
    assert extract_json('Here is the result: {"action": "drop"} done.') == {"action": "drop"}


def test_extract_json_raises_on_no_json():
    with pytest.raises(ValueError):
        extract_json("no json here at all")


def test_guard_prompt_value_free_passes_clean_text():
    guard_prompt_value_free("Column name: BIRTH_DATE. Rulebook: jitter_date.")  # no raise


def test_guard_prompt_value_free_blocks_value_markers():
    with pytest.raises(PromptValueLeakError):
        guard_prompt_value_free('{"raw_value": "1990-01-01"}')
    with pytest.raises(PromptValueLeakError):
        guard_prompt_value_free("row carries _phi_scrubbed marker")
