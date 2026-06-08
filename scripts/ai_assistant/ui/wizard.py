"""Setup wizard: LLM config, plugin study load, 3-step setup flow."""

from __future__ import annotations

import html
import logging
import subprocess
import sys
from typing import Any

import streamlit as st

import config
from scripts.ai_assistant.agent_graph import reset_agent
from scripts.ai_assistant.ui.bundle_status import (
    bundle_readiness_issues,
    held_set_notice,
    published_bundle_exists,
)
from scripts.ai_assistant.ui.providers import (
    _OTHER_MODEL_OPTION,
    _PROVIDER_CONFIG,
    _build_ollama_selector_state,
    _default_provider_label,
    _get_ollama_models,
)
from scripts.ai_assistant.ui.snapshot_select import (
    SnapshotActivationError,
    activate_snapshot,
    available_snapshots,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def inject_wizard_css() -> None:
    """Hide sidebar and center the wizard column."""
    with st.container(key="rpln_ui_bridge_wizard"):
        st.iframe(
            "<!doctype html><html><body style='margin:0;overflow:hidden'>"
            "<script>try{window.parent.document.body.classList.add('rpln-wizard');"
            "window.parent.document.body.classList.remove('rpln-redesign');}catch(e){}</script>"
            "</body></html>",
            width="content",
            height="content",
            tab_index=-1,
        )
    st.markdown(
        "<style>"
        "body.rpln-wizard [data-testid='stSidebar']{display:none!important;}"
        "body.rpln-wizard [data-testid='collapsedControl']{display:none!important;}"
        "body.rpln-wizard section.stMain{"
        "margin-left:0!important;max-width:100%!important;"
        "display:flex!important;flex-direction:column!important;"
        "align-items:stretch!important;justify-content:center!important;"
        "height:100vh!important;box-sizing:border-box!important;"
        "padding-top:53px!important;padding-bottom:0!important;}"
        "body.rpln-wizard section.stMain .block-container{"
        "padding-top:0!important;padding-bottom:0!important;min-height:auto!important;}"
        "body.rpln-wizard section.stMain .block-container > div.stVerticalBlock{"
        "gap:0!important;}"
        "body.rpln-wizard{overflow-x:hidden;}"
        "</style>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# LLM config helpers
# ---------------------------------------------------------------------------


def apply_llm_config(provider_label: str, api_key: str, model: str) -> None:
    """Persist provider/model selection + stash the API key in the KeyStore.

    The non-secret bits (LLM_PROVIDER, LLM_MODEL) still live in env vars
    + the config module so the rest of the app can read them at any time.
    The API key goes into the in-memory ``KeyStore`` only — never into
    ``os.environ``. ``agent_graph._build_llm`` reads it from there at
    client-construction time and passes it as ``api_key=`` explicitly.
    """
    import os

    from scripts.ai_assistant.keystore import (
        get_keystore,
        provider_slug_for,
    )

    cfg = _PROVIDER_CONFIG[provider_label]

    # Defensive: if a stale ``*_API_KEY`` was left in ``os.environ`` by a
    # previous build of the app or by the user's shell, scrub it. We keep
    # the user's *original* shell-set value separately readable through the
    # password input (see step 1 of the wizard) but we never carry it into
    # the running process env.
    for _pcfg in _PROVIDER_CONFIG.values():
        _ev = _pcfg.get("env_var")
        if _ev:
            os.environ.pop(_ev, None)

    if cfg["needs_key"] and api_key:
        slug = provider_slug_for(cfg["provider"])
        if slug is not None:
            get_keystore().set(slug, api_key)

    # Non-secret config remains in env + module attribute for compatibility
    # with code paths that read it directly.
    os.environ["LLM_PROVIDER"] = cfg["provider"]
    os.environ["LLM_MODEL"] = model
    config.LLM_PROVIDER = cfg["provider"]  # type: ignore[attr-defined]
    config.LLM_MODEL = model  # type: ignore[attr-defined]
    reset_agent()


def ensure_llm_config() -> None:
    """Re-apply non-secret LLM env vars on every Streamlit rerun.

    The KeyStore is persisted in ``st.session_state`` so keys survive
    reruns automatically — this function only refreshes the non-secret
    LLM_PROVIDER / LLM_MODEL env vars + module attributes. If the user
    pasted a key on this rerun cycle it has already been routed through
    :func:`apply_llm_config` → KeyStore.
    """
    import os

    from scripts.ai_assistant.keystore import (
        get_keystore,
        provider_slug_for,
    )

    provider_label = st.session_state.get("llm_provider_label", _default_provider_label())
    api_key = st.session_state.get("api_key_saved", "")
    model = st.session_state.get("llm_model", config.LLM_MODEL)
    if provider_label not in _PROVIDER_CONFIG:
        return
    cfg = _PROVIDER_CONFIG[provider_label]

    for _pcfg in _PROVIDER_CONFIG.values():
        _ev = _pcfg.get("env_var")
        if _ev:
            os.environ.pop(_ev, None)

    # If the password input held a value but ``apply_llm_config`` was never
    # called (e.g. coming back from a saved session), copy into the keystore
    # now so ``agent_graph`` finds it.
    if cfg["needs_key"] and api_key:
        slug = provider_slug_for(cfg["provider"])
        if slug is not None and not get_keystore().has(slug):
            get_keystore().set(slug, api_key)

    os.environ["LLM_PROVIDER"] = cfg["provider"]
    os.environ["LLM_MODEL"] = model
    config.LLM_PROVIDER = cfg["provider"]  # type: ignore[attr-defined]
    config.LLM_MODEL = model  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Study plugin activation
# ---------------------------------------------------------------------------


def _ensure_phi_key() -> None:
    """Bootstrap the PHI HMAC key if it does not yet exist."""
    if config.PHI_KEY_PATH.exists():
        return
    from scripts.security.phi_scrub import bootstrap_key

    bootstrap_key()
    logger.info("Bootstrapped PHI HMAC key at %s", config.PHI_KEY_PATH)


def _run_plugin_subprocess(
    label: str,
    cmd: list[str],
    *,
    subprocess_env: dict[str, str],
) -> tuple[bool, list[str]]:
    """Run one report-ai-study-pipeline plugin phase and return log lines."""

    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        cwd=str(config.BASE_DIR),
        env=subprocess_env,
    )
    lines = [f"[{label}]", result.stdout.strip(), result.stderr.strip()]
    return result.returncode == 0, [line for line in lines if line]


def run_pipeline() -> dict[str, Any]:
    """Activate the report-ai-study-pipeline plugin from the Load Study button.

    The web UI Load Study path is the local operator entry point for the
    portable plugin workflow. It activates the repo-bundled plugin phases in
    order, then keeps the existing post-load behavior: a verified published
    ``llm_source`` bundle is required before chat can start.

    Provider keys, when present, are injected only into this subprocess via
    the KeyStore. The parent's env stays clean before, during, and after the
    call.
    """
    import os

    from scripts.ai_assistant.keystore import (
        ENV_VAR_BY_PROVIDER,
        get_keystore,
    )

    _ensure_phi_key()

    subprocess_env = os.environ.copy()
    subprocess_env.update(get_keystore().env_for_subprocess(list(ENV_VAR_BY_PROVIDER)))

    plugin_manifest = config.BASE_DIR / "plugins" / "report-ai-study-pipeline" / "plugin.yaml"
    parts = [
        "[report-ai-study-pipeline plugin]",
        f"manifest={plugin_manifest}",
        "phase_order=excel-duplicate-handler -> sot-lean-generator -> dataset-to-llm-source",
    ]
    if not plugin_manifest.is_file():
        combined = "\n".join(
            [
                *parts,
                "Plugin manifest not found; cannot activate study-preparation plugin.",
            ]
        )
        return {"success": False, "output": combined}

    study = config.STUDY_NAME
    dataset_dir = config.BASE_DIR / "data" / "raw" / study / "datasets"
    lock_temp_files = sorted(dataset_dir.glob("~$*.xls*")) if dataset_dir.is_dir() else []
    if lock_temp_files:
        ok, logs = _run_plugin_subprocess(
            "excel-duplicate-handler",
            [
                sys.executable,
                str(
                    config.BASE_DIR
                    / "skills"
                    / "excel-duplicate-handler"
                    / "scripts"
                    / "merge_excel_duplicates.py"
                ),
                "--study",
                study,
                "--dataset-dir",
                str(dataset_dir),
                "--artifact-root",
                str(config.BASE_DIR),
            ],
            subprocess_env=subprocess_env,
        )
        parts.extend(logs)
        if not ok:
            return {"success": False, "output": "\n".join(parts).strip()}
    else:
        parts.extend(
            [
                "[excel-duplicate-handler]",
                "No Excel lock/temp dataset artifacts found; duplicate preflight did not need a merge run.",
            ]
        )

    phase_commands = [
        (
            "sot-lean-generator",
            [
                sys.executable,
                str(config.BASE_DIR / "scripts" / "source_truth" / "generate_lean_outputs.py"),
                "--study",
                study,
                "--repo-root",
                str(config.BASE_DIR),
            ],
        ),
        (
            "dataset-to-llm-source run",
            [
                sys.executable,
                str(config.BASE_DIR / "scripts" / "skills" / "extract_to_llm_source.py"),
                "run",
                "--study",
                study,
            ],
        ),
        (
            "dataset-to-llm-source verify",
            [
                sys.executable,
                str(config.BASE_DIR / "scripts" / "skills" / "extract_to_llm_source.py"),
                "verify",
                "--study",
                study,
            ],
        ),
    ]

    for label, cmd in phase_commands:
        ok, logs = _run_plugin_subprocess(label, cmd, subprocess_env=subprocess_env)
        parts.extend(logs)
        if not ok:
            return {"success": False, "output": "\n".join(parts).strip()}

    success = published_bundle_exists()
    combined = "\n".join(part for part in parts if part).strip()
    if not success:
        issues = bundle_readiness_issues()
        issue_text = "\n".join(f"- {issue}" for issue in issues)
        combined = (
            combined
            + "\n[study bundle check]\n"
            + "Plugin activation finished, but a complete llm_source bundle was not found."
        )
        if issue_text:
            combined = combined + "\nExpected outputs still missing:\n" + issue_text
    return {"success": success, "output": combined}


def _pipeline_output_exists() -> bool:
    return published_bundle_exists()


def _render_pipeline_log() -> None:
    """Render the setup log as a user-controlled, scrollable panel."""
    if not st.session_state.pipeline_log:
        return

    open_key = "pipeline_log_open"
    label = "Hide processing log" if st.session_state[open_key] else "Show processing log"
    icon = ":material/visibility_off:" if st.session_state[open_key] else ":material/receipt_long:"
    if st.button(label, key="rpln_pipeline_log_toggle", icon=icon, width="stretch"):
        st.session_state[open_key] = not st.session_state[open_key]
        st.rerun()

    if st.session_state[open_key]:
        with st.container(height=280, border=True):
            st.code(st.session_state.pipeline_log, language="")


# ---------------------------------------------------------------------------
# Held-set notice + snapshot selector (W2)
# ---------------------------------------------------------------------------


def _render_held_set_notice() -> None:
    """Show a NON-BLOCKING warning when the latest run held forms for review.

    Advisory only: the operator may keep querying the already-published approved
    sets. The UI never triggers a retry/resume from here — held forms are
    resolved by a maintainer in CLI mode, never from this surface.
    """
    try:
        notice = held_set_notice(config.STUDY_NAME)
    except Exception:  # pragma: no cover - advisory path must never crash chat
        logger.debug("held_set_notice raised; suppressing for the UI", exc_info=True)
        notice = None
    if notice:
        st.warning(notice, icon="⚠️")


def _render_snapshot_selector() -> None:
    """Render a dropdown to load a previously written immutable study snapshot.

    Selecting a snapshot calls :func:`activate_snapshot`, which re-runs the PHI
    leak gate and repoints the assistant read zone at that snapshot's
    ``llm_source/`` ONLY. No scrub/retry/resume path is ever invoked.
    """
    try:
        snapshots = available_snapshots(config.STUDY_NAME)
    except Exception:  # pragma: no cover - advisory path must never crash chat
        logger.debug("available_snapshots raised; suppressing for the UI", exc_info=True)
        snapshots = []
    if not snapshots:
        return

    _placeholder = "Use live pipeline output"

    def _label(entry: dict[str, Any]) -> str:
        passed = "verified" if entry.get("verifier_passed") else "unverified"
        return (
            f"{entry['id']} — {entry.get('approved_count', 0)} approved, "
            f"{entry.get('held_count', 0)} held ({passed})"
        )

    options = [_placeholder] + [entry["id"] for entry in snapshots]
    labels = {entry["id"]: _label(entry) for entry in snapshots}

    selected = st.selectbox(
        "Existing study data (snapshot)",
        options,
        index=0,
        format_func=lambda opt: opt if opt == _placeholder else labels.get(opt, opt),
        help=(
            "Load a previously reviewed, immutable clean-pass snapshot instead of "
            "the live pipeline output. Only the snapshot's PHI-scrubbed data is "
            "exposed; its approval/manifest stay private."
        ),
    )

    if (
        selected is not None
        and selected != _placeholder
        and selected != st.session_state.get("active_snapshot_id")
    ):
        try:
            activate_snapshot(config.STUDY_NAME, selected)
        except SnapshotActivationError as exc:
            st.error(f"Could not load snapshot: {exc}")
            return
        st.session_state["active_snapshot_id"] = selected
        st.session_state.pipeline_ready = True
        reset_agent()
        st.toast(f"Loaded snapshot {selected}.", icon="✅")
        st.rerun()


# ---------------------------------------------------------------------------
# Wizard header
# ---------------------------------------------------------------------------


def _render_wizard_header(step: int) -> None:
    def _pill(n: int, label: str) -> str:
        cls = "done" if step > n else ("active" if step == n else "")
        mark = "✓ " if step > n else f"{n} · "
        return f'<span class="step-pill {cls}">{mark}{label}</span>'

    st.markdown(
        '<div class="rpln-wizard-head">'
        '<div class="welcome-icon">🔬</div>'
        '<h1 class="rpln-wizard-wordmark">RePORT AI Portal</h1>'
        '<p class="rpln-wizard-tagline">AI Assistant</p>'
        f'<div class="step-pills">{_pill(1, "LLM")}{_pill(2, "Data")}{_pill(3, "Chat")}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main wizard entry point
# ---------------------------------------------------------------------------


def render_setup_page() -> None:
    """Render the 3-step setup wizard."""
    inject_wizard_css()

    _, center, _ = st.columns([1, 2, 1])
    with center:
        step = st.session_state.wizard_step
        _render_wizard_header(step)

        with st.container(key="rpln_wizard_card"):
            # ---------------------------------------------------------------- #
            # Step 1 — LLM configuration                                        #
            # ---------------------------------------------------------------- #
            if step == 1:
                st.markdown(
                    '<p class="welcome-title">Configure your AI model</p>'
                    '<p class="welcome-desc">Choose the LLM provider and paste your API key.</p>',
                    unsafe_allow_html=True,
                )

                _provider_keys = list(_PROVIDER_CONFIG.keys())
                _saved_provider = st.session_state.get(
                    "llm_provider_label", _default_provider_label()
                )
                provider_label: str = st.selectbox(
                    "Provider",
                    _provider_keys,
                    index=_provider_keys.index(_saved_provider)
                    if _saved_provider in _provider_keys
                    else 0,
                )

                cfg = _PROVIDER_CONFIG[provider_label]

                if cfg["needs_key"]:
                    # If the user already exported the provider's key in
                    # their shell (common in dev), pre-fill the input from
                    # os.environ so they don't have to re-paste on every
                    # Streamlit hot-reload. We only read env vars here — we
                    # never write the entered key back to disk (PHI rule).
                    import os as _os

                    _env_var = cfg.get("env_var") or ""
                    _seeded = st.session_state.get("api_key_saved", "")
                    if not _seeded and _env_var and _os.environ.get(_env_var):
                        _seeded = _os.environ[_env_var]
                        st.session_state["api_key_saved"] = _seeded
                    api_key: str = st.text_input(
                        f"API Key  ({cfg['env_var']})",
                        type="password",
                        value=_seeded,
                        placeholder=f"Paste your {cfg['env_var']} here",
                    )  # pyright: ignore[reportAssignmentType]
                else:
                    api_key = ""
                    st.info(
                        "Ollama runs locally — no API key required.",
                        icon=":material/info:",
                    )

                session_provider = st.session_state.get(
                    "llm_provider_label",
                    _default_provider_label(),
                )
                provider_changed = session_provider != provider_label
                saved_model = (
                    "" if provider_changed else st.session_state.get("llm_model", config.LLM_MODEL)
                )
                configured_model = (
                    config.LLM_MODEL if cfg["provider"] == config.LLM_PROVIDER else ""
                )

                is_ollama = provider_label == "Ollama (local)"
                hidden_models: list[str] = []
                model_source = "static"
                fallback_hint = cfg["default_model"]
                if is_ollama:
                    discovered_models, model_source = _get_ollama_models()
                    selector_state = _build_ollama_selector_state(
                        discovered_models,
                        source=model_source,
                        saved_model=saved_model,
                        configured_model=configured_model,  # pyright: ignore[reportArgumentType]
                        default_model=cfg["default_model"],
                    )
                    model_list = selector_state["options"]
                    model_index = int(selector_state["index"])
                    fallback_hint = str(selector_state["fallback_hint"])
                    hidden_models = list(selector_state["hidden_models"])
                else:
                    model_list = cfg.get(
                        "models",
                        [cfg["default_model"], _OTHER_MODEL_OPTION],
                    )
                    if saved_model in model_list:
                        model_index = model_list.index(saved_model)
                    else:
                        model_index = (
                            model_list.index(_OTHER_MODEL_OPTION)
                            if _OTHER_MODEL_OPTION in model_list
                            else 0
                        )

                selected_model: str = st.selectbox(
                    "Model",
                    model_list,
                    index=model_index,
                    help=(
                        "Installed Ollama chat models detected on this machine."
                        if is_ollama and model_source in {"api", "cli"}
                        else None
                    ),
                )
                if is_ollama and model_source == "fallback":
                    st.warning(
                        "Could not detect installed Ollama models. "
                        "Tried auto-starting Ollama but it did not respond. "
                        "Install Ollama from [ollama.com](https://ollama.com) and pull a model: "
                        "`ollama pull qwen3:8b`",
                        icon="⚠️",
                    )
                elif hidden_models:
                    st.caption(
                        f"Hid {len(hidden_models)} Ollama embedding/reranker tag"
                        f"{'s' if len(hidden_models) != 1 else ''} from the chat selector."
                    )

                if selected_model == _OTHER_MODEL_OPTION:
                    model: str = st.text_input(
                        "Model name",
                        value=saved_model if saved_model not in model_list else "",
                        placeholder=fallback_hint,
                    )
                    if not model:
                        model = fallback_hint
                else:
                    model = selected_model

                key_ok = (not cfg["needs_key"]) or bool(api_key)

                cost_hint = cfg.get("cost_hint")
                if cost_hint:
                    st.caption(f"💡 {cost_hint}")

                if not key_ok:
                    st.caption("⚠ Enter your API key to continue.")

                if st.button(
                    "Next →",
                    type="primary",
                    disabled=not key_ok,
                    width="stretch",
                ):
                    st.session_state.llm_provider_label = provider_label
                    st.session_state.api_key_saved = api_key
                    st.session_state.llm_model = model
                    apply_llm_config(provider_label, api_key, model)
                    st.session_state.wizard_step = 2
                    st.rerun()

            # ---------------------------------------------------------------- #
            # Step 2 — Plugin / study data                                      #
            # ---------------------------------------------------------------- #
            elif step == 2:
                st.markdown(
                    '<p class="welcome-title">Load study</p>'
                    '<p class="welcome-desc">Activate the study-preparation plugin to prepare '
                    "the published llm_source bundle for querying.</p>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<span class="rpln-beta-note">'
                    "<em>The plugin builds Source Truth from PDFs and headers, then publishes "
                    "PHI-safe datasets through the trusted host path.</em>"
                    "</span>",
                    unsafe_allow_html=True,
                )

                output_exists = _pipeline_output_exists()
                pipeline_ready: bool = st.session_state.pipeline_ready

                if pipeline_ready:
                    st.success("Study bundle loaded — ready for querying.", icon="✅")
                elif output_exists:
                    st.info(
                        "Existing complete study bundle detected at `output/`. "
                        "Run Load Study to refresh it, or proceed if it is current.",
                        icon=":material/info:",
                    )
                else:
                    st.info(
                        "No complete study bundle is on disk yet. "
                        "Run Load Study to activate the plugin and produce `llm_source/`.",
                        icon=":material/info:",
                    )

                # Non-blocking held-set notice (advisory; never gates querying).
                _render_held_set_notice()

                # Existing study data: select a reviewed, immutable snapshot.
                _render_snapshot_selector()

                # ── Load Study: activate the report-ai-study-pipeline plugin. ──
                load_label = "Reload Study" if pipeline_ready or output_exists else "Load Study"
                if st.button(
                    load_label,
                    type="primary" if not output_exists else "secondary",
                    width="stretch",
                ):
                    with st.spinner("Activating study plugin — this may take a minute..."):
                        result = run_pipeline()
                    st.session_state.pipeline_log = result["output"]
                    if result["success"]:
                        st.session_state.pipeline_ready = True
                        st.session_state.pipeline_log_open = False
                        st.toast("Study bundle loaded successfully.", icon="✅")
                        st.rerun()
                    else:
                        st.session_state.pipeline_log_open = True
                        st.error("Study load failed. Review the log below.")

                _render_pipeline_log()

                col_back, col_next = st.columns(2)
                with col_back:
                    if st.button("← Back", width="stretch"):
                        st.session_state.wizard_step = 1
                        st.rerun()
                with col_next:
                    if st.button(
                        "Next →",
                        type="primary",
                        disabled=not st.session_state.pipeline_ready,
                        width="stretch",
                    ):
                        st.session_state.wizard_step = 3
                        st.rerun()

            # ---------------------------------------------------------------- #
            # Step 3 — Confirm and start chatting                               #
            # ---------------------------------------------------------------- #
            elif step == 3:
                provider_display = html.escape(str(st.session_state.get("llm_provider_label", "")))
                model_display = html.escape(str(st.session_state.get("llm_model", "")))
                st.markdown(
                    '<p class="welcome-title">Ready to go!</p>'
                    f'<p class="welcome-desc">You\'re using <strong>{provider_display}</strong> '
                    f'— <span class="rpln-ready-model">{model_display}</span>. '
                    "Study bundle is loaded.</p>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Start Chatting →",
                    type="primary",
                    width="stretch",
                ):
                    st.session_state.setup_complete = True
                    # WP-F.05.01 — flip redesign gate on wizard exit.
                    st.session_state.chat_started = True
                    st.rerun()

                if st.button("← Back", width="stretch"):
                    st.session_state.wizard_step = 2
                    st.rerun()
