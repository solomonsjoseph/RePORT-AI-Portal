"""Session-state bootstrap and conversation helpers for RePORT AI Portal chat UI."""

from __future__ import annotations

from typing import Any, cast

import streamlit as st

import config
from scripts.ai_assistant.ui.providers import _default_provider_label


def init_state() -> None:
    """Initialize session_state idempotently on every Streamlit rerun."""
    ss = st.session_state
    defaults: dict[str, Any] = {
        # Thread / messages
        "messages": [],
        "messages_meta": {},
        # Disk-conversation identity (matches conversations.py key)
        "current_conversation_id": _new_id(),
        "current_conversation_title": "New conversation",
        # Sidebar search
        "sidebar_search_query": "",
        # Composer
        "rpln_composer_prefill": "",
        # Ended flag → goodbye page
        "ended": False,
        # Sidebar open/collapsed
        "sidebar_open": True,
        "sidebar_collapsed": False,
        # Search modal
        "rpln_search_modal_open": False,
        # Settings panel
        "rpln_settings_open": False,
        # LLM configuration (wizard)
        "setup_complete": False,
        "pipeline_ready": False,
        "pipeline_log": "",
        "pipeline_log_open": False,
        "wizard_step": 1,
        "llm_provider_label": _default_provider_label(),
        "llm_model": config.LLM_MODEL,
        "api_key_saved": "",
        # Rename helper
        "rename_target": None,
        "rename_value": "",
        # Health cache
        "_health_cache": None,
        "_health_ts": 0.0,
    }
    for key, val in defaults.items():
        if key not in ss:
            ss[key] = val

    # Keep thread_id in sync with current_conversation_id (agent graph uses thread_id)
    ss["thread_id"] = ss["current_conversation_id"]

    # Auto-detect pipeline output so returning users skip wizard step 2
    if not ss.pipeline_ready and _pipeline_output_exists():
        ss.pipeline_ready = True


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _pipeline_output_exists() -> bool:
    try:
        return config.STUDY_LLM_SOURCE_DIR.exists() and any(config.TRIO_DATASETS_DIR.glob("*.jsonl"))
    except Exception:
        return False


def get_meta(idx: int) -> dict[str, Any]:
    """Return (and lazily create) the meta dict for message at index `idx`."""
    return cast(
        "dict[str, Any]",
        st.session_state.messages_meta.setdefault(
            idx, {"feedback": None, "edited": False, "tools_used": None}
        ),
    )
