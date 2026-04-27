"""Study Knowledge Base — YAML-driven ground truth for variable mappings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.ai_assistant.file_access import validate_agent_read

_DEFAULT_YAML = Path(__file__).resolve().parents[2] / "config" / "study_knowledge.yaml"


class StudyKnowledge:
    """Provides deterministic lookups for variable mappings, value encodings,
    dataset relationships, and outcome definitions from study_knowledge.yaml."""

    def __init__(self, yaml_path: Path | None = None) -> None:
        path = yaml_path or _DEFAULT_YAML
        if not path.is_file():
            raise FileNotFoundError(f"Study knowledge YAML not found: {path}")
        validated = validate_agent_read(path)
        with validated.open() as fh:
            self._data: dict[str, Any] = yaml.safe_load(fh)
        self._cohorts: dict[str, Any] = self._data.get("cohorts", {})
        self._datasets: dict[str, Any] = self._data.get("dataset_relationships", {})
        self._study: dict[str, Any] = self._data.get("study", {})

    # ── public API ──────────────────────────────────────────────────

    @property
    def study_name(self) -> str:
        return str(self._study.get("name", ""))

    @property
    def study_description(self) -> str:
        return str(self._study.get("description", ""))

    def list_cohorts(self) -> list[str]:
        return list(self._cohorts.keys())

    def get_cohort(self, cohort_id: str) -> dict[str, Any]:
        if cohort_id not in self._cohorts:
            raise ValueError(f"Unknown cohort '{cohort_id}'. Available: {self.list_cohorts()}")
        return dict(self._cohorts[cohort_id])

    def list_concepts(self, cohort_id: str) -> list[str]:
        cohort = self.get_cohort(cohort_id)
        concepts: list[str] = []
        concepts.extend(cohort.get("demographics", {}).keys())
        concepts.extend(cohort.get("predictors", {}).keys())
        return concepts

    def resolve_concept(self, concept: str, cohort_id: str) -> dict[str, Any]:
        cohort = self.get_cohort(cohort_id)
        # Search demographics first, then predictors
        for section_key in ("demographics", "predictors"):
            section = cohort.get(section_key, {})
            if concept in section:
                result = dict(section[concept])
                result["section"] = section_key
                return result
        raise KeyError(
            f"Unknown concept '{concept}' in cohort '{cohort_id}'. "
            f"Available: {self.list_concepts(cohort_id)}"
        )

    def get_outcome(self, cohort_id: str, outcome_name: str) -> dict[str, Any]:
        cohort = self.get_cohort(cohort_id)
        outcomes = cohort.get("outcomes", {})
        if outcome_name not in outcomes:
            raise KeyError(
                f"Unknown outcome '{outcome_name}' in cohort '{cohort_id}'. "
                f"Available: {list(outcomes.keys())}"
            )
        return dict(outcomes[outcome_name])

    def get_value_encoding(self, column: str, cohort_id: str) -> dict[str, Any]:
        cohort = self.get_cohort(cohort_id)
        for section_key in ("demographics", "predictors"):
            for info in cohort.get(section_key, {}).values():
                if info.get("column") == column:
                    result: dict[str, Any] = {"column": column, "type": info.get("type")}
                    if "encoding" in info:
                        result["encoding"] = info["encoding"]
                    if "binary_map" in info:
                        result["binary_map"] = info["binary_map"]
                    if "valid_range" in info:
                        result["valid_range"] = info["valid_range"]
                    return result
        raise KeyError(f"Column '{column}' not found in cohort '{cohort_id}'")

    def get_join_plan(self, cohort_id: str, concepts: list[str]) -> list[dict[str, Any]]:
        cohort = self.get_cohort(cohort_id)
        datasets_needed: dict[str, set[str]] = {}

        # Always need screening dataset for demographics
        screening = cohort.get("screening_dataset", "")
        if screening:
            datasets_needed[screening] = set()

        for concept in concepts:
            try:
                info = self.resolve_concept(concept, cohort_id)
                ds = info.get("dataset", "")
                col = info.get("column", "")
                if ds:
                    datasets_needed.setdefault(ds, set())
                    if col:
                        datasets_needed[ds].add(col)
            except KeyError:
                # Check derived variables
                derived = cohort.get("derived_variables", {})
                if concept in derived:
                    for source in derived[concept].get("sources", []):
                        try:
                            src_info = self.resolve_concept(source, cohort_id)
                            ds = src_info.get("dataset", "")
                            col = src_info.get("column", "")
                            if ds:
                                datasets_needed.setdefault(ds, set())
                                if col:
                                    datasets_needed[ds].add(col)
                        except KeyError:
                            pass

        join_key = self._datasets.get("join_key", "SUBJID")
        plan: list[dict[str, Any]] = []
        for ds, cols in datasets_needed.items():
            ds_info = self._datasets.get("datasets", {}).get(ds, {})
            plan.append(
                {
                    "dataset": ds,
                    "columns": sorted(cols) if cols else ds_info.get("key_columns", []),
                    "join_key": join_key,
                    "form": ds_info.get("form", ""),
                    "description": ds_info.get("description", ""),
                }
            )
        return plan

    def get_derived_variable(self, name: str, cohort_id: str) -> dict[str, Any]:
        cohort = self.get_cohort(cohort_id)
        derived = cohort.get("derived_variables", {})
        if name not in derived:
            raise KeyError(
                f"Unknown derived variable '{name}' in cohort '{cohort_id}'. "
                f"Available: {list(derived.keys())}"
            )
        return dict(derived[name])

    def get_default_outcome(self, cohort_id: str) -> tuple[str, dict[str, Any]]:
        cohort = self.get_cohort(cohort_id)
        outcomes = cohort.get("outcomes", {})
        if not outcomes:
            raise ValueError(f"No outcomes defined for cohort '{cohort_id}'")
        name = next(iter(outcomes))
        return name, dict(outcomes[name])
