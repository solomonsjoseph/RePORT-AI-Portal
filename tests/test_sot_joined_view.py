"""Tests for derived PDF+dataset query views."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.ai_assistant.sot_joined_view import (
    build_joined_query_view,
    find_dataset_schema_for_policy,
    write_joined_query_view_yaml,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_policy(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
study: Indo-US VAP Biomarkers
form:
  number: Form 6
  title: HIV Treatment and CD4 Enumeration
variables:
  HIV_CD4DAT:
    section: form_body
    pdf_question: 3a. CD4 Test Date
    type: date
    description: CD4 test date (Day / Month / Year)
    relationships:
      - "if yes \u2192 HIV_SIGN \u2014 completion"
  HIV_SIGN:
    section: completion
    pdf_question: 'Signature of Data Collector:'
    type: signature
    phi: drop
    description: Data collector signature line
""".lstrip(),
        encoding="utf-8",
    )


def _write_schema(path: Path, columns: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "study": "Indo-VAP",
                "form": "6_HIV",
                "source_dataset": "data/raw/Indo-VAP/datasets/6_HIV.xlsx",
                "jsonl_file": "tmp/6_HIV.jsonl",
                "record_count": 1401,
                "columns": columns,
                "runtime_fields": [
                    {
                        "name": "source_file",
                        "description": "source workbook filename retained for LLM traceability",
                        "published_in_jsonl": True,
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_joined_view_combines_pdf_and_dataset_metadata(tmp_path: Path) -> None:
    policy = tmp_path / "6_HIV_policy.yaml"
    schema = tmp_path / "6_HIV_schema.json"
    _write_policy(policy)
    _write_schema(
        schema,
        [
            {
                "name": "HIV_CD4DAT",
                "source_order": 12,
                "phi_action": "jitter_date",
                "published_in_jsonl": True,
                "llm_status": "available",
            },
            {
                "name": "HIV_SIGN",
                "source_order": 17,
                "phi_action": "drop",
                "published_in_jsonl": False,
                "llm_status": "not_available_dropped",
            },
        ],
    )

    view = build_joined_query_view(policy, schema)

    assert view["variables"]["HIV_CD4DAT"] == {
        "pdf": {
            "section": "form_body",
            "question": "3a. CD4 Test Date",
            "type": "date",
            "description": "CD4 test date (Day / Month / Year)",
            "relationships": ["if yes -> HIV_SIGN - completion"],
        },
        "dataset": {
            "phi_action": "jitter_date",
        },
    }
    assert view["dataset"] == {
        "source_dataset": "data/raw/Indo-VAP/datasets/6_HIV.xlsx",
        "jsonl_file": "tmp/6_HIV.jsonl",
        "record_count": 1401,
    }


def test_joined_view_rejects_duplicate_schema_columns(tmp_path: Path) -> None:
    policy = tmp_path / "6_HIV_policy.yaml"
    schema = tmp_path / "6_HIV_schema.json"
    _write_policy(policy)
    _write_schema(
        schema,
        [
            {"name": "HIV_CD4DAT", "source_order": 12, "phi_action": "jitter_date"},
            {"name": "HIV_CD4DAT", "source_order": 13, "phi_action": "retain"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate dataset schema column name"):
        build_joined_query_view(policy, schema)


def test_joined_view_scrubs_llm_status_and_jsonl_publish_flags(tmp_path: Path) -> None:
    policy = tmp_path / "6_HIV_policy.yaml"
    schema = tmp_path / "6_HIV_schema.json"
    _write_policy(policy)
    _write_schema(
        schema,
        [
            {
                "name": "HIV_CD4DAT",
                "source_order": 12,
                "phi_action": "jitter_date",
                "published_in_jsonl": True,
                "llm_status": "available",
            },
        ],
    )

    rendered = json.dumps(build_joined_query_view(policy, schema))

    assert "llm_status" not in rendered
    assert "published_in_jsonl" not in rendered
    assert "source_order" not in rendered


def test_joined_view_yaml_writer_keeps_relationships_llm_readable(tmp_path: Path) -> None:
    policy = tmp_path / "6_HIV_policy.yaml"
    schema = tmp_path / "6_HIV_schema.json"
    out = tmp_path / "6_HIV_joined_query_view.yaml"
    _write_policy(policy)
    _write_schema(
        schema,
        [
            {
                "name": "HIV_CD4DAT",
                "source_order": 12,
                "phi_action": "jitter_date",
            },
        ],
    )

    write_joined_query_view_yaml(out, build_joined_query_view(policy, schema))
    text = out.read_text(encoding="utf-8")

    assert r"\u2192" not in text
    assert r"\u2014" not in text
    assert "if yes -> HIV_SIGN - completion" in text


def test_skill_script_writes_llm_readable_joined_view(tmp_path: Path) -> None:
    policy = tmp_path / "6_HIV_policy.yaml"
    schema = tmp_path / "6_HIV_schema.json"
    out = tmp_path / "6_HIV_joined_query_view.yaml"
    _write_policy(policy)
    _write_schema(
        schema,
        [
            {
                "name": "HIV_CD4DAT",
                "source_order": 12,
                "phi_action": "jitter_date",
                "published_in_jsonl": True,
                "llm_status": "available",
            },
        ],
    )

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "skills/sot-lean-generator/scripts/generate_joined_query_view.py",
            "--policy",
            str(policy),
            "--schema",
            str(schema),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "if yes -> HIV_SIGN - completion" in text
    assert "llm_status" not in text
    assert "published_in_jsonl" not in text
    assert "source_order" not in text


def test_schema_discovery_supports_sot_pair_layout(tmp_path: Path) -> None:
    pair_dir = tmp_path / "SoT" / "6_HIV"
    policy = pair_dir / "pdf" / "6_HIV_policy.yaml"
    schema = pair_dir / "dataset" / "6_HIV_schema.json"
    _write_policy(policy)
    _write_schema(schema, [{"name": "HIV_CD4DAT", "source_order": 12}])

    assert find_dataset_schema_for_policy(policy) == schema
