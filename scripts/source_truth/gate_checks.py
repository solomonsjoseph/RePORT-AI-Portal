from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GateFinding",
    "check_c_phi_ledger_alignment",
    "check_d_phi_action_mismatch",
    "check_g_phi_dropped_vars_absent",
]

DROP_ACTIONS: frozenset[str] = frozenset({"drop", "birthdate_drop"})


@dataclass(frozen=True)
class GateFinding:
    check: str
    form: str
    variable_id: str
    issue: str
    fix_paths: tuple[str, ...]


def check_c_phi_ledger_alignment(
    declared_entries: list[dict],
    as_written_events: list[dict],
) -> list[GateFinding]:
    declared_keys = {(e["form"], e["variable_id"]) for e in declared_entries}
    aw_keys = {(e["form"], e["variable_id"]) for e in as_written_events}

    findings: list[GateFinding] = []

    for form, variable_id in declared_keys - aw_keys:
        findings.append(
            GateFinding(
                check="C",
                form=form,
                variable_id=variable_id,
                issue="declared PHI event has no as-written counterpart",
                fix_paths=(),
            )
        )

    for form, variable_id in aw_keys - declared_keys:
        findings.append(
            GateFinding(
                check="C",
                form=form,
                variable_id=variable_id,
                issue="as-written PHI event has no declared counterpart",
                fix_paths=(),
            )
        )

    return sorted(findings, key=lambda f: (f.form, f.variable_id))


def check_d_phi_action_mismatch(
    declared_entries: list[dict],
    as_written_events: list[dict],
) -> list[GateFinding]:
    aw_by_key: dict[tuple[str, str], str] = {
        (e["form"], e["variable_id"]): e["action"] for e in as_written_events
    }

    findings: list[GateFinding] = []

    for entry in declared_entries:
        key = (entry["form"], entry["variable_id"])
        if key not in aw_by_key:
            continue
        declared_action = entry["action"]
        aw_action = aw_by_key[key]
        if declared_action != aw_action:
            findings.append(
                GateFinding(
                    check="D",
                    form=key[0],
                    variable_id=key[1],
                    issue=f"declared action {declared_action!r} != as-written action {aw_action!r}",
                    fix_paths=(),
                )
            )

    return sorted(findings, key=lambda f: (f.form, f.variable_id))


def check_g_phi_dropped_vars_absent(
    as_written_events: list[dict],
    scrubbed_cols_by_form: dict[str, frozenset[str]],
) -> list[GateFinding]:
    findings: list[GateFinding] = []

    for event in as_written_events:
        if event["action"] not in DROP_ACTIONS:
            continue
        form = event["form"]
        var = event["variable_id"]
        if var in scrubbed_cols_by_form.get(form, frozenset()):
            findings.append(
                GateFinding(
                    check="G",
                    form=form,
                    variable_id=var,
                    issue=f"PHI-dropped variable {var!r} still present in scrubbed dataset",
                    fix_paths=(),
                )
            )

    return sorted(findings, key=lambda f: (f.form, f.variable_id))
