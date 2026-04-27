"""Analytical Engine — Pre-built, deterministic epidemiological analysis modules.

These are pure Python functions — no LLM involvement. They produce the same
results regardless of which model or orchestration mode calls them.
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import matplotlib

matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from matplotlib.lines import Line2D

from scripts.ai_assistant.file_access import validate_agent_read, validate_agent_write
from scripts.ai_assistant.study_knowledge import StudyKnowledge

warnings.filterwarnings("ignore", category=FutureWarning)

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _load_jsonl(path: Path) -> pd.DataFrame:
    # Zone-guard the read; any path outside trio_bundle/ or agent/ raises.
    validated = validate_agent_read(path)
    with open(validated) as f:
        return pd.DataFrame([json.loads(line) for line in f])


def _apply_binary_map(series: pd.Series, binary_map: dict[str, list[str]]) -> pd.Series:
    """Map text values to 0/1 using positive/negative lists (case-insensitive)."""
    s = series.astype(str).str.strip().str.lower()
    pos = {v.strip().lower() for v in binary_map.get("positive", [])}
    neg = {v.strip().lower() for v in binary_map.get("negative", [])}

    def _map(val: str) -> float | None:
        if val in pos:
            return 1.0
        if val in neg:
            return 0.0
        return None

    return s.map(_map)


def _apply_encoding(series: pd.Series, encoding: dict[str, int]) -> pd.Series:
    """Map text values to integers (case-insensitive)."""
    s = series.astype(str).str.strip().str.lower()
    enc_lower = {k.strip().lower(): v for k, v in encoding.items()}
    return s.map(enc_lower)


# ── CohortBuilder ────────────────────────────────────────────────────────


class CohortBuilder:
    """Load, join, and recode datasets into an analytic DataFrame."""

    def __init__(self, knowledge: StudyKnowledge, data_dir: Path) -> None:
        self.knowledge = knowledge
        self.data_dir = Path(data_dir)

    def build(
        self,
        cohort_id: str,
        concepts: list[str],
        outcome: str,
        timeout_fn: Callable[..., None] | None = None,
    ) -> pd.DataFrame:
        cohort = self.knowledge.get_cohort(cohort_id)
        subject_id = cohort.get("subject_id", "SUBJID")

        # 1. Load screening demographics
        screening_ds = cohort["screening_dataset"]
        scr = _load_jsonl(self.data_dir / screening_ds)
        logger.debug("Loaded screening: %s (%d rows)", screening_ds, len(scr))

        # Build base from demographics
        demo_info = cohort.get("demographics", {})
        rename_map: dict[str, str] = {}
        keep_cols = [subject_id]
        for concept_name, info in demo_info.items():
            col = info["column"]
            if col in scr.columns:
                keep_cols.append(col)
                rename_map[col] = f"{concept_name}_raw"
        scr = scr[keep_cols].copy()
        scr.rename(columns=rename_map, inplace=True)
        if timeout_fn:
            timeout_fn("screening loaded")

        # 2. Load baseline
        baseline_ds = cohort["baseline_dataset"]
        bl = _load_jsonl(self.data_dir / baseline_ds)
        bl_cols = [subject_id]
        predictors_info = cohort.get("predictors", {})
        for info in predictors_info.values():
            if info.get("dataset") == baseline_ds:
                col = info["column"]
                if col in bl.columns:
                    bl_cols.append(col)
        bl = bl[bl_cols].copy()
        logger.debug("Loaded baseline: %s (%d rows)", baseline_ds, len(bl))
        if timeout_fn:
            timeout_fn("baseline loaded")

        # 3. Join screening + baseline
        df = scr.merge(bl, on=subject_id, how="inner")
        logger.debug("Joined screening+baseline: %d rows", len(df))
        if timeout_fn:
            timeout_fn("screening+baseline merged")

        # 4. Load HbA1c from CBC (first per subject)
        hba1c_info = predictors_info.get("hba1c")
        if hba1c_info and "hba1c" in concepts:
            cbc_ds = hba1c_info["dataset"]
            cbc_col = hba1c_info["column"]
            cbc_path = self.data_dir / cbc_ds
            if cbc_path.exists():
                cbc = _load_jsonl(cbc_path)[[subject_id, cbc_col]].copy()
                cbc[cbc_col] = _to_numeric(cbc[cbc_col])
                cbc = cbc.dropna(subset=[cbc_col]).groupby(subject_id).first().reset_index()
                df = df.merge(cbc, on=subject_id, how="left")
            if timeout_fn:
                timeout_fn("CBC merged")

        # 5. Load outcome
        outcome_info = self.knowledge.get_outcome(cohort_id, outcome)
        outcome_ds = outcome_info["dataset"]
        outcome_col = outcome_info["column"]
        positive_labels = set(outcome_info.get("positive_labels", []))

        foa = _load_jsonl(self.data_dir / outcome_ds)[[subject_id, outcome_col]].copy()

        def _worst_outcome(group: pd.DataFrame) -> int:
            return int(any(group[outcome_col].isin(positive_labels)))

        agg = (
            foa.groupby(subject_id)
            .apply(_worst_outcome, include_groups=False)  # type: ignore[call-overload]
            .reset_index(name=outcome)
        )

        # Handle additional outcome sources (Cohort B: FUB_TBDIAG)
        additional = outcome_info.get("additional_sources", [])
        for src in additional:
            src_ds = src["dataset"]
            src_col = src["column"]
            src_pos = set(src.get("positive_values", []))
            src_path = self.data_dir / src_ds
            if src_path.exists():
                extra = _load_jsonl(src_path)[[subject_id, src_col]].copy()
                extra[src_col] = _to_numeric(extra[src_col])
                extra_agg = (
                    extra.groupby(subject_id)[src_col]
                    .apply(
                        lambda x, _pos=src_pos: int(any(v in _pos for v in x.values)),
                        include_groups=False,
                    )
                    .reset_index(name=f"_extra_{outcome}")
                )
                agg = agg.merge(extra_agg, on=subject_id, how="left")
                agg[outcome] = agg[[outcome, f"_extra_{outcome}"]].max(axis=1).fillna(0).astype(int)
                agg.drop(columns=[f"_extra_{outcome}"], inplace=True)

        # Determine join type based on aggregation
        join_how = "inner" if outcome_info.get("aggregation") == "worst_per_subject" else "left"
        df = df.merge(agg, on=subject_id, how=join_how)  # type: ignore[arg-type]
        if join_how == "left":
            df[outcome] = df[outcome].fillna(0).astype(int)
        logger.debug(
            "After outcome join: %d rows, %d events",
            len(df),
            int(df[outcome].sum()) if outcome in df.columns else 0,
        )
        if timeout_fn:
            timeout_fn("outcome loaded")

        # 6. Recode variables
        age_col = demo_info.get("age", {}).get("column")
        age_raw_name = "age_raw" if "age_raw" in df.columns else None
        if age_raw_name:
            df["age"] = _to_numeric(df[age_raw_name])
        elif age_col and age_col in df.columns:
            df["age"] = _to_numeric(df[age_col])

        sex_info = demo_info.get("sex", {})
        sex_raw_name = "sex_raw" if "sex_raw" in df.columns else sex_info.get("column")
        if sex_raw_name and sex_raw_name in df.columns:
            encoding = sex_info.get("encoding", {})
            df["sex"] = _apply_encoding(df[sex_raw_name], encoding)

        # Recode predictors
        for concept_name, info in predictors_info.items():
            col = info["column"]
            if col not in df.columns:
                continue
            analysis_name = info.get("analysis_name", concept_name)

            if "binary_map" in info:
                df[analysis_name] = _apply_binary_map(df[col], info["binary_map"])
            elif info.get("type") == "ordinal":
                valid = info.get("valid_range", [])
                s = _to_numeric(df[col])
                if valid:
                    df[analysis_name] = s.where(s.isin(valid))
                else:
                    df[analysis_name] = s
            elif info.get("type") == "continuous":
                df[analysis_name] = _to_numeric(df[col])

        # 7. Derived variables (BMI)
        derived = cohort.get("derived_variables", {})
        if "bmi" in derived and "bmi" in concepts:
            bmi_info = derived["bmi"]
            height_info = predictors_info.get("height", {})
            weight_info = predictors_info.get("weight", {})
            knee_info = predictors_info.get("knee_height", {})

            height = _to_numeric(df.get(height_info.get("column", ""), pd.Series(dtype=float)))
            weight = _to_numeric(df.get(weight_info.get("column", ""), pd.Series(dtype=float)))

            # Mark implausible heights as missing
            plausible = height_info.get("plausible_range", [50, 250])
            if len(plausible) >= 1:
                height = height.where(height >= plausible[0])

            # Estimate from knee height if missing
            knee_col = knee_info.get("column", "")
            if knee_col and knee_col in df.columns:
                knee = _to_numeric(df[knee_col])
                age_num = df.get("age", pd.Series(dtype=float))
                est_h = 2.02 * knee - 0.04 * age_num + 64.19
                height = height.fillna(est_h)

            h_m = height / 100.0
            bmi_vals = weight / (h_m**2)
            bmi_range = bmi_info.get("plausible_range", [10, 60])
            df["bmi"] = bmi_vals.where(bmi_vals.between(bmi_range[0], bmi_range[1]))

        # Malnutrition: binary derived from BMI (WHO adult undernutrition = BMI < 18.5)
        if "malnutrition" in derived and "malnutrition" in concepts and "bmi" in df.columns:
            mal_info = derived["malnutrition"]
            threshold = float(mal_info.get("threshold", 18.5))
            bmi_col = df["bmi"]
            mal = (bmi_col < threshold).astype(float)
            mal[bmi_col.isna()] = float("nan")
            df["malnutrition"] = mal

        # HbA1c rename
        if hba1c_info and hba1c_info["column"] in df.columns:
            df["hba1c"] = df[hba1c_info["column"]]

        # 8. Select final columns
        keep = [subject_id, outcome]
        keep.extend(
            c
            for c in [
                "age",
                "sex",
                "smoking",
                "diabetes",
                "alcohol_freq",
                "bmi",
                "malnutrition",
                "hba1c",
            ]
            if c in df.columns
        )
        if timeout_fn:
            timeout_fn("cohort recoded")
        return df[[c for c in keep if c in df.columns]].copy()


# ── UnivariateAnalyzer ──────────────────────────────────────────────────


class UnivariateAnalyzer:
    """Run univariate logistic regression for each predictor."""

    def run(
        self,
        df: pd.DataFrame,
        outcome: str,
        predictors: list[str],
    ) -> pd.DataFrame:
        results = [self._single(df, outcome, pred) for pred in predictors]
        out = pd.DataFrame(results)
        out["significant"] = out["p_value"] < 0.05
        return out

    @staticmethod
    def _single(df: pd.DataFrame, outcome: str, predictor: str) -> dict[str, Any]:
        sub = df[[outcome, predictor]].dropna()
        base = {"predictor": predictor, "n": len(sub)}
        if sub[outcome].nunique() < 2 or len(sub) < 20:
            return {
                **base,
                "coef": np.nan,
                "OR": np.nan,
                "p_value": np.nan,
                "ci_lo": np.nan,
                "ci_hi": np.nan,
            }
        x_mat = sm.add_constant(sub[predictor].astype(float))
        y = sub[outcome].astype(float)
        try:
            model = sm.Logit(y, x_mat).fit(disp=0, maxiter=100)
            coef = model.params[predictor]
            ci = model.conf_int().loc[predictor]
            return {
                **base,
                "coef": coef,
                "OR": np.exp(coef),
                "p_value": model.pvalues[predictor],
                "ci_lo": np.exp(ci[0]),
                "ci_hi": np.exp(ci[1]),
            }
        except Exception:
            return {
                **base,
                "coef": np.nan,
                "OR": np.nan,
                "p_value": np.nan,
                "ci_lo": np.nan,
                "ci_hi": np.nan,
            }


# ── MultivariateAnalyzer ────────────────────────────────────────────────


class MultivariateAnalyzer:
    """Backward stepwise logistic regression."""

    def run(
        self,
        df: pd.DataFrame,
        outcome: str,
        predictors: list[str],
        alpha: float = 0.05,
    ) -> dict[str, Any]:
        current = list(predictors)
        sub_check = df[[outcome, *current]].dropna()
        current = [p for p in current if sub_check[p].nunique() > 1]
        if not current:
            return {
                "error": "No predictors with variation after dropping NaN",
                "converged": False,
                "n": 0,
                "retained_predictors": [],
            }

        sub = df[[outcome, *current]].dropna()
        if sub[outcome].nunique() < 2:
            return {
                "error": f"Outcome '{outcome}' has no variation",
                "converged": False,
                "n": len(sub),
                "retained_predictors": [],
            }

        model = None
        iteration = 0
        while True:
            iteration += 1
            logger.debug("Backward step %d: %d predictors remaining", iteration, len(current))
            x_mat = sm.add_constant(sub[current].astype(float))
            y = sub[outcome].astype(float)
            try:
                model = sm.Logit(y, x_mat).fit(disp=0, maxiter=200)
            except Exception as e:
                if model is None:
                    return {
                        "error": f"Initial model fit failed: {e}",
                        "converged": False,
                        "n": len(sub),
                        "retained_predictors": [],
                        "recommendation": "Consider Firth's penalized regression for rare events",
                    }
                break
            pvals = model.pvalues.drop("const", errors="ignore")
            worst_p = pvals.max()
            if worst_p > alpha and len(current) > 1:
                drop = pvals.idxmax()
                current.remove(drop)
                logger.debug("  Dropped %s (p=%.4f)", drop, worst_p)
                sub = df[[outcome, *current]].dropna()
            else:
                break

        if model is None:
            return {
                "error": "Model fitting failed",
                "converged": False,
                "n": 0,
                "retained_predictors": [],
            }

        # Build summary table
        retained = [p for p in model.params.index if p != "const"]
        rows = []
        for p in retained:
            orv = np.exp(model.params[p])
            ci = np.exp(model.conf_int().loc[p])
            rows.append(
                {
                    "predictor": p,
                    "OR": orv,
                    "ci_lo": ci[0],
                    "ci_hi": ci[1],
                    "p_value": model.pvalues[p],
                }
            )

        return {
            "retained_predictors": retained,
            "summary_table": pd.DataFrame(rows),
            "model_summary_text": str(model.summary2()),
            "aic": model.aic,
            "bic": model.bic,
            "pseudo_r2": model.prsquared,
            "n": int(model.nobs),
            "converged": model.mle_retvals.get("converged", True)
            if hasattr(model, "mle_retvals")
            else True,
        }


# ── InteractionAnalyzer ─────────────────────────────────────────────────


class InteractionAnalyzer:
    """Logistic regression with interaction terms."""

    def run(
        self,
        df: pd.DataFrame,
        outcome: str,
        factors: list[str],
        moderators: list[str],
    ) -> pd.DataFrame:
        results: list[dict[str, Any]] = []
        results.extend(
            self._single(df, outcome, factor, moderator)
            for factor in factors
            for moderator in moderators
        )
        out = pd.DataFrame(results)
        out["significant"] = out["interaction_p"] < 0.05
        return out

    @staticmethod
    def _single(
        df: pd.DataFrame,
        outcome: str,
        factor: str,
        moderator: str,
    ) -> dict[str, Any]:
        sub = df[[outcome, factor, moderator]].dropna()
        base = {"factor": factor, "moderator": moderator, "n": len(sub)}
        if len(sub) < 30 or sub[outcome].nunique() < 2:
            return {
                **base,
                "factor_p": np.nan,
                "moderator_p": np.nan,
                "interaction_p": np.nan,
                "interaction_OR": np.nan,
            }
        sub = sub.copy()
        sub["interact"] = sub[factor].astype(float) * sub[moderator].astype(float)
        x_mat = sm.add_constant(sub[[factor, moderator, "interact"]].astype(float))
        y = sub[outcome].astype(float)
        try:
            model = sm.Logit(y, x_mat).fit(disp=0, maxiter=200)
            return {
                **base,
                "factor_p": model.pvalues.get(factor, np.nan),
                "moderator_p": model.pvalues.get(moderator, np.nan),
                "interaction_p": model.pvalues.get("interact", np.nan),
                "interaction_OR": np.exp(model.params.get("interact", np.nan)),
            }
        except Exception:
            return {
                **base,
                "factor_p": np.nan,
                "moderator_p": np.nan,
                "interaction_p": np.nan,
                "interaction_OR": np.nan,
            }


# ── DescriptiveAnalyzer ─────────────────────────────────────────────────


class DescriptiveAnalyzer:
    """Summary statistics and frequency tables."""

    def run(self, df: pd.DataFrame, predictors: list[str]) -> dict[str, Any]:
        stats: dict[str, Any] = {"n": len(df)}
        for pred in predictors:
            if pred not in df.columns:
                continue
            col = df[pred].dropna()
            if col.nunique() <= 5:  # categorical/ordinal
                stats[pred] = {
                    "type": "categorical",
                    "counts": col.value_counts().to_dict(),
                    "n_valid": len(col),
                    "n_missing": df[pred].isna().sum(),
                }
            else:  # continuous
                stats[pred] = {
                    "type": "continuous",
                    "mean": float(col.mean()),
                    "std": float(col.std()),
                    "median": float(col.median()),
                    "min": float(col.min()),
                    "max": float(col.max()),
                    "n_valid": len(col),
                    "n_missing": df[pred].isna().sum(),
                }
        return stats


# ── PlotGenerator ────────────────────────────────────────────────────────


@dataclass(slots=True)
class PlotArtifacts:
    """Saved artifacts for a generated analysis plot."""

    interactive: Path | None = None
    static: Path | None = None


class PlotGenerator:
    """Generate analysis plots."""

    PLOT_TYPES: ClassVar[dict[str, str]] = {
        "violin": "Violin plot for categorical predictor vs binary outcome",
        "scatter": "Scatter/strip plot for continuous predictor vs binary outcome",
        "interaction_violin": "Violin paneled by age-group and sex",
        "interaction_scatter": "Scatter colored by sex, sized by age",
    }

    def generate(
        self,
        df: pd.DataFrame,
        outcome: str,
        predictor: str,
        plot_type: str,
        save_dir: Path,
        **kwargs: Any,
    ) -> PlotArtifacts | None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        base_name = f"{plot_type}_{outcome}_{predictor}"
        json_path = save_dir / f"{base_name}.json"
        png_path = save_dir / f"{base_name}.png"

        generated_interactive = False
        if plot_type == "violin":
            generated_interactive = self._plotly_violin(df, outcome, predictor, json_path)
            if not generated_interactive:
                self._violin(df, outcome, predictor, png_path)
        elif plot_type == "scatter":
            generated_interactive = self._plotly_scatter(df, outcome, predictor, json_path)
            if not generated_interactive:
                self._scatter(df, outcome, predictor, png_path)
        elif plot_type == "interaction_violin":
            generated_interactive = self._plotly_interaction_violin(
                df,
                outcome,
                predictor,
                json_path,
            )
            if not generated_interactive:
                self._interaction_violin(df, outcome, predictor, png_path)
        elif plot_type == "interaction_scatter":
            generated_interactive = self._plotly_interaction_scatter(
                df,
                outcome,
                predictor,
                json_path,
            )
            if not generated_interactive:
                self._interaction_scatter(df, outcome, predictor, png_path)
        else:
            return None

        artifacts = PlotArtifacts(
            interactive=json_path if json_path.exists() else None,
            static=png_path if png_path.exists() else None,
        )
        return artifacts if artifacts.interactive or artifacts.static else None

    @staticmethod
    def _save_plotly_json(fig: Any, save_path: Path) -> bool:
        """Persist a Plotly figure as JSON. Return False when Plotly is unavailable."""
        try:
            import plotly.io as pio
        except ImportError:
            return False
        validated = validate_agent_write(save_path)
        validated.write_text(pio.to_json(fig), encoding="utf-8")
        return True

    @staticmethod
    def _categorical_labels(values: list[Any]) -> dict[Any, str]:
        """Map binary values to readable labels while preserving other categories."""
        if set(values) <= {0, 1}:
            return {0: "No", 1: "Yes"}
        return {value: str(value) for value in values}

    @staticmethod
    def _plotly_violin(df: pd.DataFrame, outcome: str, factor: str, save_path: Path) -> bool:
        try:
            import plotly.express as px
        except ImportError:
            return False

        sub = df[[outcome, factor]].dropna().copy()
        if sub.empty:
            return False

        categories = sorted(sub[factor].unique())
        labels = PlotGenerator._categorical_labels(categories)
        sub["_factor_label"] = sub[factor].map(labels).fillna(sub[factor].astype(str))
        sub["_outcome_label"] = (
            sub[outcome].map({0: "No", 1: "Yes"}).fillna(sub[outcome].astype(str))
        )
        fig = px.violin(
            sub,
            x="_factor_label",
            y=outcome,
            color="_outcome_label",
            box=True,
            points="all",
            category_orders={"_factor_label": [labels.get(cat, str(cat)) for cat in categories]},
            labels={"_factor_label": factor, outcome: outcome, "_outcome_label": outcome},
            title=f"{outcome} vs {factor}",
        )
        fig.update_traces(pointpos=0, jitter=0.22, marker={"size": 5, "opacity": 0.55})
        fig.update_layout(showlegend=True)
        fig.update_yaxes(tickvals=[0, 1], ticktext=["No", "Yes"])
        return PlotGenerator._save_plotly_json(fig, save_path)

    @staticmethod
    def _plotly_scatter(df: pd.DataFrame, outcome: str, factor: str, save_path: Path) -> bool:
        try:
            import plotly.express as px
        except ImportError:
            return False

        sub = df[[outcome, factor]].dropna().copy()
        if sub.empty:
            return False

        rng = np.random.default_rng(7)
        sub["_outcome_jitter"] = sub[outcome] + rng.normal(0, 0.06, len(sub))
        sub["_outcome_label"] = (
            sub[outcome].map({0: "No", 1: "Yes"}).fillna(sub[outcome].astype(str))
        )
        fig = px.scatter(
            sub,
            x=factor,
            y="_outcome_jitter",
            color="_outcome_label",
            labels={factor: factor, "_outcome_jitter": outcome, "_outcome_label": outcome},
            title=f"{outcome} vs {factor}",
            opacity=0.65,
        )
        fig.update_yaxes(tickvals=[0, 1], ticktext=["No", "Yes"])
        return PlotGenerator._save_plotly_json(fig, save_path)

    @staticmethod
    def _plotly_interaction_violin(
        df: pd.DataFrame,
        outcome: str,
        factor: str,
        save_path: Path,
    ) -> bool:
        try:
            import plotly.express as px
        except ImportError:
            return False

        sub = df[[outcome, factor, "age", "sex"]].dropna().copy()
        if sub.empty or sub[outcome].nunique() < 2:
            return False

        categories = sorted(sub[factor].unique())
        labels = PlotGenerator._categorical_labels(categories)
        sub["_factor_label"] = sub[factor].map(labels).fillna(sub[factor].astype(str))
        sub["age_group"] = pd.cut(
            sub["age"],
            bins=[0, 25, 45, 100],
            labels=["≤25", "26-45", ">45"],
        )
        sub["sex_label"] = sub["sex"].map({1: "Male", 0: "Female"}).fillna("Unknown")
        fig = px.strip(
            sub,
            x="_factor_label",
            y=outcome,
            color="age_group",
            facet_col="sex_label",
            stripmode="overlay",
            labels={"_factor_label": factor, outcome: outcome, "age_group": "Age group"},
            title=f"{outcome} vs {factor} by age and sex",
        )
        fig.update_traces(jitter=0.24, marker={"size": 6, "opacity": 0.62})
        fig.update_yaxes(tickvals=[0, 1], ticktext=["No", "Yes"])
        return PlotGenerator._save_plotly_json(fig, save_path)

    @staticmethod
    def _plotly_interaction_scatter(
        df: pd.DataFrame,
        outcome: str,
        factor: str,
        save_path: Path,
    ) -> bool:
        try:
            import plotly.express as px
        except ImportError:
            return False

        sub = df[[outcome, factor, "age", "sex"]].dropna().copy()
        if sub.empty:
            return False

        rng = np.random.default_rng(19)
        sub["_outcome_jitter"] = sub[outcome] + rng.normal(0, 0.06, len(sub))
        sub["sex_label"] = sub["sex"].map({1: "Male", 0: "Female"}).fillna("Unknown")
        fig = px.scatter(
            sub,
            x=factor,
            y="_outcome_jitter",
            color="sex_label",
            size="age",
            hover_data={"age": True, factor: True, "_outcome_jitter": False},
            labels={factor: factor, "_outcome_jitter": outcome, "sex_label": "Sex"},
            title=f"{outcome} vs {factor} by age and sex",
            opacity=0.68,
        )
        fig.update_yaxes(tickvals=[0, 1], ticktext=["No", "Yes"])
        return PlotGenerator._save_plotly_json(fig, save_path)

    @staticmethod
    def _violin(df: pd.DataFrame, outcome: str, factor: str, save_path: Path) -> None:
        sub = df[[outcome, factor]].dropna()
        if sub.empty:
            return
        fig, ax = plt.subplots(figsize=(7, 5))
        categories = sorted(sub[factor].unique())
        for i, cat in enumerate(categories):
            y0 = np.random.normal(
                0, 0.05, (sub[(sub[factor] == cat) & (sub[outcome] == 0)].shape[0],)
            )
            y1 = np.random.normal(
                1, 0.05, (sub[(sub[factor] == cat) & (sub[outcome] == 1)].shape[0],)
            )
            ax.scatter([i - 0.15] * len(y0), y0, alpha=0.3, s=10, c="steelblue")
            ax.scatter([i + 0.15] * len(y1), y1, alpha=0.3, s=10, c="tomato")
        ax.violinplot(
            [sub.loc[sub[factor] == cat, outcome].values for cat in categories],
            showmeans=True,
            showmedians=True,
        )
        ax.set_xticks(range(len(categories)))
        labels = (
            {0: "No", 1: "Yes"} if set(categories) <= {0, 1} else {c: str(c) for c in categories}
        )
        ax.set_xticklabels([labels.get(c, str(c)) for c in categories])
        ax.set_ylabel(outcome)
        ax.set_xlabel(factor)
        ax.set_title(f"{outcome} ~ {factor}")
        n1 = int((sub[outcome] == 1).sum())
        ax.text(
            0.98,
            0.98,
            f"n={len(sub)} (event={n1})",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=9,
            color="gray",
        )
        fig.tight_layout()
        fig.savefig(validate_agent_write(save_path), dpi=150, bbox_inches="tight")
        plt.close(fig)

    @staticmethod
    def _scatter(df: pd.DataFrame, outcome: str, factor: str, save_path: Path) -> None:
        sub = df[[outcome, factor]].dropna()
        if sub.empty:
            return
        fig, ax = plt.subplots(figsize=(7, 5))
        jitter = np.random.normal(0, 0.08, len(sub))
        ax.scatter(sub[factor], sub[outcome] + jitter, alpha=0.3, s=15, c="steelblue")
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["No", "Yes"])
        ax.set_ylabel(outcome)
        ax.set_xlabel(factor)
        ax.set_title(f"{outcome} ~ {factor}")
        n1 = int((sub[outcome] == 1).sum())
        ax.text(
            0.98,
            0.98,
            f"n={len(sub)} (event={n1})",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=9,
            color="gray",
        )
        fig.tight_layout()
        fig.savefig(validate_agent_write(save_path), dpi=150, bbox_inches="tight")
        plt.close(fig)

    @staticmethod
    def _interaction_violin(df: pd.DataFrame, outcome: str, factor: str, save_path: Path) -> None:
        sub = df[[outcome, factor, "age", "sex"]].dropna()
        if sub.empty or sub[outcome].nunique() < 2:
            return
        sub = sub.copy()
        sub["age_group"] = pd.cut(sub["age"], bins=[0, 25, 45, 100], labels=["≤25", "26-45", ">45"])
        sub["sex_label"] = sub["sex"].map({1: "Male", 0: "Female"})
        fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
        for ax, (sex_label, grp) in zip(axes, sub.groupby("sex_label")):
            cats = sorted(grp[factor].unique())
            age_groups = grp["age_group"].cat.categories
            colors = {"≤25": "steelblue", "26-45": "darkorange", ">45": "forestgreen"}
            width = 0.25
            for j, ag in enumerate(age_groups):
                subset = grp[grp["age_group"] == ag]
                for i, cat in enumerate(cats):
                    vals = subset.loc[subset[factor] == cat, outcome].values
                    if len(vals) > 0:
                        jit = np.random.normal(0, 0.06, len(vals))
                        ax.scatter(
                            [i + (j - 1) * width] * len(vals),
                            vals + jit,
                            alpha=0.4,
                            s=12,
                            c=colors[ag],
                            label=ag if i == 0 else "",
                        )
            ax.set_xticks(range(len(cats)))
            lbl = {0: "No", 1: "Yes"} if set(cats) <= {0, 1} else {c: str(c) for c in cats}
            ax.set_xticklabels([lbl.get(c, str(c)) for c in cats])
            ax.set_title(f"{sex_label}")
            ax.set_xlabel(factor)
            if ax == axes[0]:
                ax.set_ylabel(outcome)
            handles = [
                Line2D(
                    [0],
                    [0],
                    marker="o",
                    color="w",
                    markerfacecolor=colors[ag],
                    markersize=8,
                    label=ag,
                )
                for ag in age_groups
            ]
            ax.legend(handles=handles, title="Age group", loc="upper right", fontsize=8)
        fig.suptitle(f"{outcome} ~ {factor} x age/sex", fontsize=13)
        fig.tight_layout()
        fig.savefig(validate_agent_write(save_path), dpi=150, bbox_inches="tight")
        plt.close(fig)

    @staticmethod
    def _interaction_scatter(df: pd.DataFrame, outcome: str, factor: str, save_path: Path) -> None:
        sub = df[[outcome, factor, "age", "sex"]].dropna()
        if sub.empty:
            return
        fig, ax = plt.subplots(figsize=(8, 5))
        jitter = np.random.normal(0, 0.06, len(sub))
        colors = sub["sex"].map({1: "steelblue", 0: "tomato"}).values
        sizes = (sub["age"] / sub["age"].max()) * 60 + 5
        ax.scatter(sub[factor], sub[outcome] + jitter, alpha=0.4, s=sizes, c=colors)  # type: ignore[arg-type]
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["No", "Yes"])
        ax.set_ylabel(outcome)
        ax.set_xlabel(factor)
        ax.set_title(f"{outcome} ~ {factor} x age/sex")
        handles = [
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor="steelblue",
                markersize=8,
                label="Male",
            ),
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor="tomato",
                markersize=8,
                label="Female",
            ),
        ]
        ax.legend(handles=handles, title="Sex", loc="upper right")
        fig.tight_layout()
        fig.savefig(validate_agent_write(save_path), dpi=150, bbox_inches="tight")
        plt.close(fig)


# ── ResultInterpreter ────────────────────────────────────────────────────


class ResultInterpreter:
    """Convert statistical output into narrative text."""

    def interpret_univariate(
        self,
        results: pd.DataFrame,
        outcome: str,
        cohort_name: str,
    ) -> str:
        lines = [f"## Univariate Logistic Regressions — {cohort_name}\n"]
        lines.append(f"Outcome: **{outcome}**\n")
        sig = results[results["significant"] == True]  # noqa: E712
        if sig.empty:
            lines.append("No predictors reached statistical significance (p < 0.05).\n")
        else:
            lines.append(f"**{len(sig)} significant predictor(s):**\n")
            for _, row in sig.iterrows():
                lines.append(
                    f"- **{row['predictor']}**: OR = {row['OR']:.3f} "
                    f"(95% CI [{row['ci_lo']:.3f}, {row['ci_hi']:.3f}]), "
                    f"p = {row['p_value']:.4f}"
                )
        lines.append("\nFull results:\n")
        lines.append("| Predictor | n | OR | 95% CI | p-value |")
        lines.append("|-----------|---|-----|--------|---------|")
        for _, row in results.iterrows():
            ci = f"[{row['ci_lo']:.3f}, {row['ci_hi']:.3f}]" if not np.isnan(row["OR"]) else "—"
            pv = f"{row['p_value']:.4f}" if not np.isnan(row["p_value"]) else "—"
            orv = f"{row['OR']:.3f}" if not np.isnan(row["OR"]) else "—"
            sig_mark = " *" if row.get("significant") else ""
            lines.append(f"| {row['predictor']} | {row['n']} | {orv} | {ci} | {pv}{sig_mark} |")
        return "\n".join(lines)

    def interpret_multivariate(self, result: dict[str, Any], cohort_name: str) -> str:
        lines = [f"## Multivariate Model (Backward Selection) — {cohort_name}\n"]
        if "error" in result:
            lines.append(f"**Model failed:** {result['error']}")
            if "recommendation" in result:
                lines.append(f"\n**Recommendation:** {result['recommendation']}")
            return "\n".join(lines)

        lines.append(f"N = {result['n']}, AIC = {result.get('aic', 'N/A'):.1f}")
        lines.append(f"\n**Retained predictors ({len(result['retained_predictors'])}):**\n")
        table = result.get("summary_table")
        if table is not None and not table.empty:
            lines.append("| Predictor | OR | 95% CI | p-value |")
            lines.append("|-----------|-----|--------|---------|")
            for _, row in table.iterrows():
                lines.append(
                    f"| **{row['predictor']}** | {row['OR']:.3f} | "
                    f"[{row['ci_lo']:.3f}, {row['ci_hi']:.3f}] | {row['p_value']:.4f} |"
                )
        return "\n".join(lines)

    def interpret_interaction(self, results: pd.DataFrame, cohort_name: str) -> str:
        lines = [f"## Interaction Models — {cohort_name}\n"]
        sig = results[results["significant"] == True]  # noqa: E712
        if sig.empty:
            lines.append("No significant interactions detected (p < 0.05).\n")
        else:
            lines.append(f"**{len(sig)} significant interaction(s):**\n")
            for _, row in sig.iterrows():
                lines.append(
                    f"- **{row['factor']} x {row['moderator']}**: "
                    f"interaction p = {row['interaction_p']:.4f}"
                )
        lines.append("\nFull results:\n")
        lines.append("| Factor | Moderator | n | Interaction p | Sig |")
        lines.append("|--------|-----------|---|---------------|-----|")
        for _, row in results.iterrows():
            pv = f"{row['interaction_p']:.4f}" if not np.isnan(row["interaction_p"]) else "—"
            sig_mark = " **" if row.get("significant") else ""
            lines.append(f"| {row['factor']} | {row['moderator']} | {row['n']} | {pv}{sig_mark} |")
        return "\n".join(lines)

    def generate_caveats(
        self,
        df: pd.DataFrame,
        outcome: str,
        cohort_name: str,
    ) -> str:
        lines = [f"## Caveats — {cohort_name}\n"]
        n = len(df)
        events = int(df[outcome].sum())
        event_rate = events / n * 100 if n > 0 else 0

        if events < 10:
            lines.append(
                f"⚠️ **Severely underpowered**: Only {events} events in {n} subjects "
                f"({event_rate:.1f}% event rate). Multivariate models may fail with "
                f"singular matrix. Consider Firth's penalized regression or exact "
                f"logistic regression."
            )
        elif events < 30:
            lines.append(
                f"⚠️ **Low power**: {events} events — limited ability to detect "
                f"small effect sizes or test multiple predictors simultaneously."
            )
        missing = df.isna().sum()
        high_missing = missing[missing > n * 0.1]
        if not high_missing.empty:
            lines.append("\n**Variables with >10% missing data:**")
            for col, cnt in high_missing.items():
                lines.append(f"- {col}: {cnt} missing ({cnt / n * 100:.1f}%)")
        return "\n".join(lines)


# ── Full Analysis Runner ────────────────────────────────────────────────


@dataclass
class AnalysisResult:
    """Container for all analysis outputs."""

    cohort_name: str
    outcome: str
    n: int = 0
    events: int = 0
    univariate: pd.DataFrame | None = None
    multivariate: dict[str, Any] | None = None
    interaction: pd.DataFrame | None = None
    descriptive: dict[str, Any] | None = None
    interactive_figures: list[Path] = field(default_factory=list)
    figures: list[Path] = field(default_factory=list)
    narrative: str = ""
    caveats: str = ""


def run_full_analysis(
    knowledge: StudyKnowledge,
    data_dir: Path,
    output_dir: Path,
    cohort_id: str,
    outcome: str | None = None,
    predictors: list[str] | None = None,
    analysis_types: list[str] | None = None,
    plot_types: list[str] | None = None,
    timeout: int = 0,
) -> AnalysisResult:
    """Run a complete analysis pipeline for a single cohort."""
    t0 = time.monotonic()

    def _check_timeout(step: str) -> None:
        elapsed = time.monotonic() - t0
        logger.info("  [%.1fs] %s", elapsed, step)
        if timeout > 0 and elapsed > timeout:
            raise TimeoutError(
                f"Analysis timed out after {elapsed:.0f}s (limit={timeout}s) during: {step}"
            )

    logger.info("=== run_full_analysis(cohort=%s) ===", cohort_id)

    # Defaults
    if outcome is None:
        outcome, _ = knowledge.get_default_outcome(cohort_id)
    if predictors is None:
        predictors = ["smoking", "diabetes", "bmi", "alcohol", "age", "sex"]
    if analysis_types is None:
        analysis_types = ["univariate", "multivariate", "interaction"]
    if plot_types is None:
        plot_types = ["violin", "scatter"]

    cohort_info = knowledge.get_cohort(cohort_id)
    cohort_name = cohort_info.get("name", cohort_id)

    # Concepts needed = predictors + their dependencies
    concepts = list(predictors)
    # Always include demographics
    for c in ["age", "sex"]:
        if c not in concepts:
            concepts.append(c)
    # Malnutrition depends on BMI
    if "malnutrition" in concepts and "bmi" not in concepts:
        concepts.append("bmi")

    # Build cohort (or load cached analytic CSV).
    # Cache freshness is gated on a sidecar manifest that records the exact
    # concept set + outcome the CSV was built for. Without the manifest check,
    # a prior call with a narrower predictor set would poison later calls that
    # need additional columns (e.g. the CSV built for {smoking,sex} wouldn't
    # contain a `bmi_mnutr_bin` column, and a later malnutrition request would
    # silently skip that predictor).
    analytic_path = output_dir / f"{cohort_id}_analytic.csv"
    manifest_path = output_dir / f"{cohort_id}_analytic.manifest.json"
    cache_key = {"concepts": sorted(set(concepts)), "outcome": outcome}

    cache_is_fresh = False
    if analytic_path.exists() and manifest_path.exists():
        try:
            recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
            cache_is_fresh = recorded == cache_key
        except (OSError, json.JSONDecodeError):
            cache_is_fresh = False

    if cache_is_fresh:
        logger.info("Loading cached analytic dataset: %s", analytic_path)
        df = pd.read_csv(validate_agent_read(analytic_path))
        _check_timeout("dataset loaded (cached)")
    else:
        builder = CohortBuilder(knowledge, data_dir)
        df = builder.build(cohort_id, concepts, outcome, timeout_fn=_check_timeout)
        _check_timeout(f"Cohort built: {len(df)} rows, {df.shape[1]} cols")
        df.to_csv(validate_agent_write(analytic_path), index=False)
        validate_agent_write(manifest_path).write_text(
            json.dumps(cache_key, sort_keys=True), encoding="utf-8"
        )
        _check_timeout("Analytic dataset saved")

    cohort_dir = validate_agent_write(output_dir / cohort_id)
    cohort_dir.mkdir(parents=True, exist_ok=True)

    result = AnalysisResult(
        cohort_name=cohort_name,
        outcome=outcome,
        n=len(df),
        events=int(df[outcome].sum()),
    )

    interpreter = ResultInterpreter()

    # Analysis name mapping
    name_map: dict[str, str] = {}
    for p_name, p_info in cohort_info.get("predictors", {}).items():
        name_map[p_name] = p_info.get("analysis_name", p_name)

    # Resolve predictor names to analysis column names
    analysis_preds: list[str] = []
    for p in predictors:
        aname = name_map.get(p, p)
        if aname in df.columns:
            analysis_preds.append(aname)

    narrative_parts: list[str] = []
    narrative_parts.append(
        f"# {cohort_name} — {outcome}\n\n"
        f"**N = {result.n}**, **Events = {result.events}** "
        f"({result.events / result.n * 100:.1f}% event rate)\n"
    )

    # Univariate
    if "univariate" in analysis_types and analysis_preds:
        uni_analyzer = UnivariateAnalyzer()
        result.univariate = uni_analyzer.run(df, outcome, analysis_preds)
        result.univariate.to_csv(cohort_dir / "univariate_results.csv", index=False)
        narrative_parts.append(
            interpreter.interpret_univariate(result.univariate, outcome, cohort_name)
        )
        _check_timeout(f"Univariate: {len(analysis_preds)} predictors")

    # Plots for univariate. Route by predictor scale: categorical → violin,
    # continuous → scatter. If the caller asked for a plot type that doesn't
    # match the predictor scale (e.g. "violin" for continuous `age`), route
    # to the scale-appropriate equivalent instead of silently dropping the
    # plot, and surface a routing note in the narrative so the LLM (and user)
    # knows the substitution happened.
    if plot_types and analysis_preds:
        plotter = PlotGenerator()
        categorical_preds = ["diabetes", "smoking", "sex"]
        continuous_preds = ["bmi", "alcohol_freq", "age", "hba1c"]
        want_violin = "violin" in plot_types
        want_scatter = "scatter" in plot_types
        plot_fallback_notes: list[str] = []
        for pred in analysis_preds:
            is_cat = pred in categorical_preds
            is_cont = pred in continuous_preds

            plot_kind: str | None = None
            fallback_from: str | None = None
            if is_cat and want_violin:
                plot_kind = "violin"
            elif is_cont and want_scatter:
                plot_kind = "scatter"
            elif is_cat and want_scatter and not want_violin:
                plot_kind = "violin"
                fallback_from = "scatter"
            elif is_cont and want_violin and not want_scatter:
                plot_kind = "scatter"
                fallback_from = "violin"

            if plot_kind is None:
                continue

            plot_artifacts = plotter.generate(df, outcome, pred, plot_kind, cohort_dir)
            if plot_artifacts is None:
                continue
            if plot_artifacts.interactive is not None:
                result.interactive_figures.append(plot_artifacts.interactive)
            if plot_artifacts.static is not None:
                result.figures.append(plot_artifacts.static)
            if fallback_from:
                pred_kind = "categorical" if is_cat else "continuous"
                plot_fallback_notes.append(
                    f"- `{pred}` ({pred_kind}) rendered as **{plot_kind}** — "
                    f"a {fallback_from} plot is not meaningful for a {pred_kind} predictor."
                )

        if plot_fallback_notes:
            narrative_parts.append(
                "### Plot routing notes\n\n"
                "Some requested plot types didn't match the predictor scale; "
                "they were auto-routed to the scale-appropriate equivalent:\n\n"
                + "\n".join(plot_fallback_notes)
            )
        _check_timeout(
            f"Univariate plots: {len(result.interactive_figures) + len(result.figures)} figures"
        )

    # Multivariate
    if "multivariate" in analysis_types and analysis_preds:
        mv_analyzer = MultivariateAnalyzer()
        result.multivariate = mv_analyzer.run(df, outcome, analysis_preds)
        if "error" not in result.multivariate:
            with open(cohort_dir / "multivariate_summary.txt", "w") as f:
                f.write(result.multivariate.get("model_summary_text", ""))
        narrative_parts.append(interpreter.interpret_multivariate(result.multivariate, cohort_name))
        _check_timeout("Multivariate complete")

    # Interaction
    if "interaction" in analysis_types and analysis_preds:
        comorbidities = [p for p in analysis_preds if p not in ("age", "sex")]
        moderators = [p for p in ("age", "sex") if p in df.columns]
        if comorbidities and moderators:
            int_analyzer = InteractionAnalyzer()
            result.interaction = int_analyzer.run(df, outcome, comorbidities, moderators)
            result.interaction.to_csv(cohort_dir / "interaction_results.csv", index=False)
            narrative_parts.append(
                interpreter.interpret_interaction(result.interaction, cohort_name)
            )
            _check_timeout("Interaction models complete")

            # Interaction plots
            if plot_types:
                plotter = PlotGenerator()
                for pred in comorbidities:
                    if pred in categorical_preds:
                        plot_artifacts = plotter.generate(
                            df, outcome, pred, "interaction_violin", cohort_dir
                        )
                        if plot_artifacts is not None:
                            if plot_artifacts.interactive is not None:
                                result.interactive_figures.append(plot_artifacts.interactive)
                            if plot_artifacts.static is not None:
                                result.figures.append(plot_artifacts.static)
                    elif pred in continuous_preds:
                        plot_artifacts = plotter.generate(
                            df, outcome, pred, "interaction_scatter", cohort_dir
                        )
                        if plot_artifacts is not None:
                            if plot_artifacts.interactive is not None:
                                result.interactive_figures.append(plot_artifacts.interactive)
                            if plot_artifacts.static is not None:
                                result.figures.append(plot_artifacts.static)
                _check_timeout(
                    "Interaction plots: "
                    f"{len(result.interactive_figures) + len(result.figures)} total"
                )

    # Descriptive
    if "descriptive" in analysis_types:
        desc = DescriptiveAnalyzer()
        result.descriptive = desc.run(df, analysis_preds)

    # Caveats
    result.caveats = interpreter.generate_caveats(df, outcome, cohort_name)
    narrative_parts.append(result.caveats)

    result.narrative = "\n\n".join(narrative_parts)

    elapsed = time.monotonic() - t0
    logger.info(
        "=== Analysis complete in %.1fs (N=%d, events=%d, figs=%d) ===",
        elapsed,
        result.n,
        result.events,
        len(result.interactive_figures) + len(result.figures),
    )

    return result
