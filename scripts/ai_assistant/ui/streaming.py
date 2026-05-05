"""LLM streaming, message rendering, and response formatting."""

from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st

import config
from scripts.ai_assistant.agent_graph import stream_query
from scripts.ai_assistant.file_access import validate_agent_read
from scripts.ai_assistant.phi_safe import (
    redact_message_content,
    redact_phi_in_text,
    sanitise_traceback,
)

logger = logging.getLogger(__name__)

# Ollama OOM surface — the ladder in agent_graph._init_llm catches this at
# boot, but if every rung also refuses (very low free RAM) the RuntimeError
# reaches here with the last-rung's message. Module-level so tests can
# import the keywords directly without faking Streamlit's runtime.
_MEMORY_KEYWORDS: tuple[str, ...] = (
    "requires more system memory",
    "out of memory",
    "insufficient memory",
)

# ---------------------------------------------------------------------------
# WP-06 CSS/JS — message actions and streaming caret
# ---------------------------------------------------------------------------

_CSS_MSG_ACTIONS: str = (
    "<style>"
    # Streaming caret
    ".rpln-caret{display:inline-block;color:var(--accent);"
    "animation:rpln-blink 1s steps(2,start) infinite;margin-left:2px;}"
    "@keyframes rpln-blink{to{visibility:hidden;}}"
    # Hover-reveal action row (assistant rail — existing)
    ".rpln-msg-actions{display:flex;gap:var(--sp-2,8px);margin-top:var(--sp-2,8px);"
    "opacity:0;transition:opacity var(--dur-fast,120ms) var(--ease-out,ease);}"
    "[data-testid='stChatMessage']:hover .rpln-msg-actions,"
    "[data-testid='stChatMessage']:focus-within .rpln-msg-actions{opacity:1;}"
    ".rpln-msg-action{background:transparent;border:1px solid var(--border-subtle);"
    "border-radius:var(--r-md,10px);padding:4px 10px;font-size:var(--fs-xs,11px);"
    "color:var(--text-secondary);cursor:pointer;"
    "transition:all var(--dur-fast,120ms) var(--ease-out,ease);}"
    ".rpln-msg-action:hover{color:var(--text-primary);"
    "border-color:var(--border-strong);background:var(--bg-sidebar-row-hover);}"
    # WP-F.04.18b: User action rail — icon-only Retry/Edit/Copy below user pill
    # Hover-reveal mirrors Claude's `opacity-0 group-hover:opacity-100` pattern.
    # Layout: flex row, right-aligned to match user pill's right-aligned anchor;
    # gap + margin match Claude's h-8 w-8 icon footprint.
    ".rpln-user-rail{display:flex;justify-content:flex-end;align-items:center;"
    "gap:4px;margin-top:6px;opacity:0;"
    "transition:opacity var(--dur-fast,120ms) var(--ease-out,ease);}"
    "[data-testid='stChatMessage']:hover .rpln-user-rail,"
    "[data-testid='stChatMessage']:focus-within .rpln-user-rail{opacity:1;}"
    ".rpln-rail-ts{font-size:12px;color:rgba(232,228,222,0.45);"
    "margin-right:6px;font-family:var(--font-sans);}"
    ".rpln-rail-btn{display:inline-flex;align-items:center;justify-content:center;"
    "width:32px;height:32px;padding:0;background:transparent;"
    "border:none;border-radius:6px;cursor:pointer;"
    "color:rgba(232,228,222,0.55);"
    "transition:background 120ms ease,color 120ms ease;}"
    ".rpln-rail-btn:hover{background:rgba(255,255,255,0.06);"
    "color:rgba(232,228,222,0.92);}"
    ".rpln-rail-btn svg{width:16px;height:16px;display:block;}"
    # WP-F.04.18c: feedback controls on the assistant rail.
    # Separator is a thin vertical rule between logical groups (Copy/Regen │
    # 👍👎). pointer-events:none prevents the sep from trapping hover.
    ".rpln-rail-sep{display:inline-block;width:1px;height:16px;"
    "background:rgba(255,255,255,0.1);margin:0 6px;pointer-events:none;}"
    # Active feedback state — filled background + brighter icon when the
    # user has toggled 👍 or 👎 on this message.
    ".rpln-rail-btn[data-rpln-fb-active='1']{color:rgba(232,228,222,0.92);"
    "background:rgba(255,255,255,0.08);}"
    # Hidden regenerate bridge — catches rpln_regen_*, rpln_regen_user_retry_*,
    # rpln_regen_user_edit_*, rpln_regen_fb_up_*, rpln_regen_fb_down_*
    # (shared prefix keeps CSS DRY).
    "[class*='st-key-rpln_regen_']{position:fixed!important;left:-9999px!important;"
    "width:1px!important;height:1px!important;overflow:hidden!important;opacity:0!important;}"
    "</style>"
)


# Feather-style icon SVG paths (16x16, stroke-based, currentColor). Inline so
# render is style-controlled and there's no extra HTTP fetch per message.
_SVG_RETRY: str = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="1 4 1 10 7 10"/>'
    '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>'
)
_SVG_EDIT: str = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
    '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>'
)
_SVG_COPY: str = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
)
# WP-F.04.18c: feedback glyphs for the assistant rail.
_SVG_THUMB_UP: str = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9A2 2 0 0 0 19.72 9H14z"/>'
    '<line x1="7" y1="22" x2="7" y2="11"/></svg>'
)
_SVG_THUMB_DOWN: str = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9A2 2 0 0 0 4.28 15H10z"/>'
    '<line x1="17" y1="2" x2="17" y2="13"/></svg>'
)
_JS_MSG_ACTIONS: str = """<!DOCTYPE html>
<html><head></head><body style="margin:0;padding:0;">
<script>
// WP-F.04.18b: Moved from st.html (which strips <script> tags in this
// Streamlit version) to st.iframe (zero-height iframe that DOES
// execute scripts). Inside the iframe we reach the parent Streamlit doc
// via `window.parent.document` to attach the click handler and find the
// bridge buttons. This fix also re-enables the previously-silent
// assistant Copy/Regenerate rail.
(function() {
    var pWin, pDoc;
    try {
        pWin = window.parent || window;
        pDoc = pWin.document;
    } catch (e) {
        // Cross-origin access blocked — should never happen for same-origin
        // Streamlit components but degrade gracefully just in case.
        pWin = window; pDoc = document;
    }
    if (pDoc._rplnMsgHandlers) return;
    pDoc._rplnMsgHandlers = true;

    function clickBridge(key) {
        var all = pDoc.querySelectorAll('[class*="st-key-' + key + '"]');
        for (var i = 0; i < all.length; i++) {
            var cls = all[i].className;
            if (cls.indexOf(key + ' ') >= 0 || cls.slice(-key.length) === key) {
                var b = all[i].querySelector('button');
                if (b) { b.click(); return true; }
            }
        }
        return false;
    }

    pDoc.body.addEventListener('click', function(e) {
        var btn = e.target.closest && e.target.closest('[data-rpln-action]');
        if (!btn) return;
        var action = btn.getAttribute('data-rpln-action');
        var row = btn.closest('.rpln-msg-actions, .rpln-user-rail');
        var idx = row ? row.getAttribute('data-rpln-msg-idx') : null;

        if (action === 'copy' || action === 'copy-user') {
            var msgEl = row && row.closest('[data-testid="stChatMessage"]');
            var contentEl = msgEl && msgEl.querySelector('[data-testid="stChatMessageContent"]');
            if (contentEl) {
                var clone = contentEl.cloneNode(true);
                clone.querySelectorAll('[class*="st-key-rpln_regen_"]').forEach(function (el) { el.remove(); });
                clone.querySelectorAll('.rpln-user-rail, .rpln-msg-actions, .msg-ts').forEach(function (el) { el.remove(); });
                var text = (clone.innerText || '').trim();
                var cb = (pWin.navigator && pWin.navigator.clipboard) || navigator.clipboard;
                if (cb) { cb.writeText(text).catch(function() {}); }
            }
        } else if (action === 'regenerate' && idx !== null) {
            clickBridge('rpln_regen_' + idx);
        } else if (action === 'retry-user' && idx !== null) {
            clickBridge('rpln_regen_user_retry_' + idx);
        } else if (action === 'edit-user' && idx !== null) {
            clickBridge('rpln_regen_user_edit_' + idx);
        } else if (action === 'up' && idx !== null) {
            clickBridge('rpln_regen_fb_up_' + idx);
        } else if (action === 'down' && idx !== null) {
            clickBridge('rpln_regen_fb_down_' + idx);
        }
    }, true);
})();
</script>
</body></html>"""


_ART_HEADER: str = '<div class="rpln-artifact-badge">Result</div>'


def _artifact_file_download(path: Path, *, kind: str, index: int) -> tuple[bytes, str, str]:
    """Return bytes, download name, and MIME for one rendered result."""
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if kind == "PLOTLY":
        return raw, f"interactive_chart_{index}.json", "application/json"
    if suffix == ".png":
        return raw, f"chart_image_{index}.png", "image/png"
    return raw, f"result_{index}{suffix or '.bin'}", "application/octet-stream"


def _render_artifact_bar(
    *,
    key: str,
    data: bytes | None = None,
    file_name: str | None = None,
    mime: str | None = None,
) -> None:
    """Render an inline result header with an optional direct download button."""
    with st.container(key=f"rpln_artifact_bar_{key}"):
        label_col, action_col = st.columns([1, 0.14], gap="small", vertical_alignment="center")
        with label_col:
            st.markdown(_ART_HEADER, unsafe_allow_html=True)
        if data and file_name and mime:
            with action_col:
                st.download_button(
                    "Download",
                    data=data,
                    file_name=file_name,
                    mime=mime,
                    key=f"rpln_artifact_download_{key}",
                    help="Download this result",
                    icon=":material/download:",
                    type="tertiary",
                    on_click="ignore",
                    width="content",
                )


# ---------------------------------------------------------------------------
# File-path sanitization for user-facing output
# ---------------------------------------------------------------------------

_FILE_REF_EXTENSIONS = "jsonl|json|pdf|png|csv|xlsx|md"
_INTERNAL_MARKER_RE = re.compile(r"<RPLN_[A-Za-z0-9_]+:[^>\r\n]*>")
_ARTIFACT_MARKER_RE = re.compile(r"<RPLN_(?:FIGURE|PLOTLY|ANALYSIS|CODE):[^>\r\n]*>")
_ABSOLUTE_FILE_REF_RE = re.compile(
    rf"(?<![\w.-])/(?:[\w.-]+/)*[\w.-]+\.(?:{_FILE_REF_EXTENSIONS})\b"
)
_RELATIVE_FILE_REF_RE = re.compile(rf"\b(?:[\w.-]+/)+[\w.-]+\.(?:{_FILE_REF_EXTENSIONS})\b")
_FILE_EXTENSION_RE = re.compile(rf"\.(?:{_FILE_REF_EXTENSIONS})\b")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _sanitize_file_refs(text: str) -> str:
    """Strip filesystem paths, .jsonl/.json extensions, and internal markers."""
    text = _INTERNAL_MARKER_RE.sub("", text)
    text = _ABSOLUTE_FILE_REF_RE.sub("", text)
    text = _RELATIVE_FILE_REF_RE.sub("", text)
    text = _FILE_EXTENSION_RE.sub("", text)
    return _EXCESS_BLANK_LINES_RE.sub("\n\n", text).strip()


def _strip_internal_markers(text: str) -> str:
    """Replace internal render markers with generic placeholders."""
    return _ARTIFACT_MARKER_RE.sub("[Artifact]", text)


# ---------------------------------------------------------------------------
# Markdown table rendering
# ---------------------------------------------------------------------------


def _render_markdown_table(table_lines: list[str], *, key: str) -> None:
    """Render a markdown table as a Streamlit dataframe."""
    try:
        # Filter out separator lines (|---|---|)
        data_lines = [line for line in table_lines if not all(c in "|- " for c in line.strip())]
        if len(data_lines) < 2:
            st.markdown("\n".join(table_lines))
            return

        headers = [h.strip() for h in data_lines[0].strip("|").split("|")]
        rows = []
        for line in data_lines[1:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(cells)

        import pandas as pd

        # Streamlit 1.56's DataFrame front-end crashes with a
        # `Cannot read properties of undefined (reading '0')` TypeError when
        # an object-dtype column mixes em-dashes, bracketed CI strings,
        # numeric-looking strings, and "<p> *" significance markers (the
        # exact shape emitted by interpret_univariate/interpret_interaction).
        # Forcing every column to clean str + filling NaN with "" gives
        # Arrow an unambiguous schema and silences the crash.
        df = pd.DataFrame(rows, columns=headers).astype(str).fillna("")
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        _render_artifact_bar(
            key=key,
            data=csv_bytes,
            file_name=f"table_result_{key}.csv",
            mime="text/csv",
        )
        st.dataframe(df, width="stretch", hide_index=True)
    except Exception:
        # Fallback to markdown rendering
        st.markdown("\n".join(table_lines))


def _render_plotly_figure(fig_path: Path, *, msg_idx: int = 0, fig_idx: int = 0) -> None:
    """Render a Plotly figure from a saved JSON file."""
    try:
        import plotly.io as pio

        raw = fig_path.read_bytes()
        fig = pio.from_json(raw.decode("utf-8"))
        # Apply dark theme to match the UI
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin={"l": 40, "r": 20, "t": 40, "b": 40},
        )
        _render_artifact_bar(
            key=f"{msg_idx}_{fig_idx}_plotly",
            data=raw,
            file_name=f"interactive_chart_{fig_idx}.json",
            mime="application/json",
        )
        st.plotly_chart(fig, width="stretch", key=f"plotly_{msg_idx}_{fig_idx}")
    except Exception as _chart_exc:
        st.warning(
            f"**Chart render failed** (`{type(_chart_exc).__name__}`): {_chart_exc}",
            icon="📉",
        )
        logger.warning("Plotly render failed: path=%s exc=%s", fig_path, _chart_exc)


def _render_analysis_tables(text: str, *, key_prefix: str) -> str:
    """Convert markdown tables in analysis results to Streamlit dataframes."""
    lines = text.split("\n")
    output_lines: list[str] = []
    in_table = False
    table_lines: list[str] = []
    table_idx = 0

    for line in lines:
        if line.strip().startswith("|") and "|" in line[1:]:
            in_table = True
            table_lines.append(line)
        else:
            if in_table and table_lines:
                table_idx += 1
                _render_markdown_table(table_lines, key=f"{key_prefix}_{table_idx}")
                table_lines = []
                in_table = False
            output_lines.append(line)

    if table_lines:
        table_idx += 1
        _render_markdown_table(table_lines, key=f"{key_prefix}_{table_idx}")

    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# Thinking block rendering (Gemini-style)
# ---------------------------------------------------------------------------


def _render_thinking_block(
    thinking_text: str,
    *,
    msg_idx: int = 0,
    tools_used: list[dict[str, str]] | None = None,
) -> None:
    """Render model thinking as a collapsible block with left-border styling."""
    lines = thinking_text.strip().split("\n")
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                sections.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    # Build HTML for the thinking content
    html_parts: list[str] = []
    for section in sections:
        # Detect bold headers like **Something** at start of section
        header_match = re.match(r"^\*\*(.+?)\*\*\s*(.*)", section, re.DOTALL)
        if header_match:
            title = _html.escape(header_match.group(1).strip())
            body = _html.escape(header_match.group(2).strip())
            html_parts.append(f'<div class="thinking-section-title">{title}</div>')
            if body:
                html_parts.append(f'<div class="thinking-section-body">{body}</div>')
        else:
            html_parts.append(f'<div class="thinking-section-body">{_html.escape(section)}</div>')

    content_html = "\n".join(html_parts)
    thinking_id = f"thinking_{msg_idx}"

    # WP-F.04.18e — contextual chip label: "Researching…" when the model
    # both reasoned AND used retrieval/analysis tools; plain "Thinking…"
    # when reasoning alone. Matches Claude-Desktop's per-response chip.
    label = "Researching" if tools_used else "Thinking"

    st.markdown(
        f'<details class="thinking-block" id="{thinking_id}">'
        f'<summary class="thinking-toggle">'
        f'<span class="thinking-icon">✦</span>'
        f'<span class="thinking-label">{label}</span>'
        f'<span class="thinking-chevron"></span>'
        f"</summary>"
        f'<div class="thinking-content">{content_html}</div>'
        f"</details>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Source extraction and rendering
# ---------------------------------------------------------------------------


def _extract_sources(tools_detail: list[dict[str, str]]) -> list[dict[str, str]]:
    """Extract dataset/form/variable sources from tool call results."""
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    for t in tools_detail:
        name = t.get("name", "")
        preview = t.get("content_preview", "")

        if name in ("query_dataset", "get_dataset_stats", "cross_reference_variables"):
            # Extract dataset names from JSON preview
            for m in re.finditer(r'"dataset"\s*:\s*"([^"]+)"', preview):
                ds = m.group(1)
                if ds not in seen:
                    seen.add(ds)
                    sources.append({"type": "dataset", "name": ds, "icon": "📊"})

        elif name in ("list_forms", "get_form_variables"):
            for m in re.finditer(r'"form_name"\s*:\s*"([^"]+)"', preview):
                fm = m.group(1)
                if fm not in seen:
                    seen.add(fm)
                    sources.append({"type": "form", "name": fm, "icon": "📋"})

        elif name == "search_variables":
            # Just note that variables reference was searched
            key = "Variables Reference"
            if key not in seen:
                seen.add(key)
                sources.append({"type": "reference", "name": key, "icon": "🔍"})

        elif name == "get_study_overview":
            key = "Study Overview"
            if key not in seen:
                seen.add(key)
                sources.append({"type": "overview", "name": key, "icon": "📊"})

        elif name == "run_python_analysis":
            key = "Python Analysis"
            if key not in seen:
                seen.add(key)
                sources.append({"type": "analysis", "name": key, "icon": "🐍"})

        elif name == "run_study_analysis":
            key = "Epidemiological Analysis"
            if key not in seen:
                seen.add(key)
                sources.append({"type": "analysis", "name": key, "icon": "🔬"})

    return sources


def _render_sources(sources: list[dict[str, str]], *, msg_idx: int = 0) -> None:
    """Render a sources section at the bottom of a message."""
    if not sources:
        return
    pills = "".join(
        f'<span class="source-pill">'
        f'<span class="source-icon">{s["icon"]}</span>'
        f'<span class="source-name">{s["name"]}</span>'
        f"</span>"
        for s in sources
    )
    st.markdown(
        f'<div class="sources-section">'
        f'<div class="sources-header">Sources</div>'
        f'<div class="sources-list">{pills}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def _analysis_codes_from_tools(tools_used: list[dict[str, str]] | None) -> list[str]:
    """Return distinct Python snippets used by run_python_analysis tool calls."""
    if not tools_used:
        return []
    codes: list[str] = []
    seen: set[str] = set()
    for tool in tools_used:
        if tool.get("name") != "run_python_analysis":
            continue
        code = (tool.get("analysis_code") or "").strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _render_analysis_code_cards(
    tools_used: list[dict[str, str]] | None,
    *,
    msg_idx: int = 0,
) -> None:
    """Render copy-friendly, foldable cards for actual analysis code."""
    codes = _analysis_codes_from_tools(tools_used)
    for idx, code in enumerate(codes, start=1):
        label = (
            "Code used for this analysis" if len(codes) == 1 else f"Code used for analysis {idx}"
        )
        text_id = f"rpln_analysis_code_{msg_idx}_{idx}"
        with st.expander(label, expanded=False, key=f"analysis_code_{msg_idx}_{idx}"):
            st.html(
                '<div class="rpln-code-card">'
                '<div class="rpln-code-card-meta">Python executed inside the local sandbox.</div>'
                f'<button type="button" class="rpln-code-copy" '
                f'data-rpln-action="copy-code" data-rpln-code-target="{text_id}">'
                '<span class="material-symbols-rounded" aria-hidden="true">content_copy</span>'
                "<span>Copy code</span>"
                "</button>"
                f'<textarea id="{text_id}" readonly spellcheck="false">'
                f"{_html.escape(code)}"
                "</textarea>"
                "</div>"
            )
            st.code(code, language="python", line_numbers=True, wrap_lines=True)


def _analysis_code_payloads(msg: Any) -> dict[str, str]:
    """Extract run_python_analysis code by LangChain tool-call id."""
    payloads: dict[str, str] = {}
    for call in getattr(msg, "tool_calls", None) or []:
        if not isinstance(call, dict) or call.get("name") != "run_python_analysis":
            continue
        args = call.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            continue
        code = args.get("code")
        call_id = str(call.get("id") or "")
        if call_id and isinstance(code, str) and code.strip():
            payloads[call_id] = code.strip()
    return payloads


def _upsert_tool_detail(
    tools_detail: list[dict[str, str]],
    *,
    name: str,
    content_preview: str,
    analysis_code: str | None = None,
) -> None:
    """Append or enrich a tool disclosure entry without duplicating names."""
    existing = next((tool for tool in tools_detail if tool.get("name") == name), None)
    if existing is None:
        detail = {"name": name, "content_preview": content_preview}
        if analysis_code:
            detail["analysis_code"] = analysis_code
        tools_detail.append(detail)
        return
    if analysis_code and not existing.get("analysis_code"):
        existing["analysis_code"] = analysis_code


def _render_message_actions(idx: int) -> None:
    """Render hover-reveal assistant rail: Copy / Regenerate | feedback.

    Feedback state persists in ``st.session_state.messages_meta[idx]["feedback"]``.
    """
    fb_state = st.session_state.messages_meta.get(idx, {}).get("feedback")
    up_active = ' data-rpln-fb-active="1"' if fb_state == "up" else ""
    down_active = ' data-rpln-fb-active="1"' if fb_state == "down" else ""
    st.markdown(
        f'<div class="rpln-msg-actions" data-rpln-msg-idx="{idx}">'
        '<button class="rpln-msg-action" data-rpln-action="copy">Copy</button>'
        '<button class="rpln-msg-action" data-rpln-action="regenerate">Regenerate</button>'
        '<span class="rpln-rail-sep"></span>'
        f'<button class="rpln-rail-btn" data-rpln-action="up" title="Helpful"{up_active}>{_SVG_THUMB_UP}</button>'
        f'<button class="rpln-rail-btn" data-rpln-action="down" title="Not helpful"{down_active}>{_SVG_THUMB_DOWN}</button>'
        "</div>",
        unsafe_allow_html=True,
    )
    pending_stream = bool(st.session_state.get("rpln_pending_stream"))
    if st.button("↺", key=f"rpln_regen_{idx}", help="Regenerate response"):
        messages = st.session_state.messages
        if 0 < idx < len(messages) and messages[idx - 1]["role"] == "user":
            st.session_state.pending_question = messages[idx - 1]["content"]
            st.session_state.messages = list(messages[: idx - 1])
            st.session_state.messages_meta = {
                k: v for k, v in st.session_state.messages_meta.items() if k < idx - 1
            }
            st.rerun()
    # Feedback bridges. Toggle-on-repeat: clicking the same thumb twice
    # clears; clicking the opposite thumb flips. Space label (not ".") —
    # some screen readers say "period" for isolated full-stops.
    if st.button(" ", key=f"rpln_regen_fb_up_{idx}", help="Helpful") and not pending_stream:
        meta = st.session_state.messages_meta.setdefault(idx, {})
        meta["feedback"] = None if meta.get("feedback") == "up" else "up"
        st.rerun()
    if st.button(" ", key=f"rpln_regen_fb_down_{idx}", help="Not helpful") and not pending_stream:
        meta = st.session_state.messages_meta.setdefault(idx, {})
        meta["feedback"] = None if meta.get("feedback") == "down" else "down"
        st.rerun()


def _render_user_message_actions(idx: int, timestamp_display: str = "") -> None:
    """Render WP-F.04.18b user-rail below a user pill.

    Three icon-only buttons (Retry / Edit / Copy) revealed on hover of the
    parent ``stChatMessage``. Retry re-submits the message at ``idx`` as a
    new turn (truncates history from idx onward). Edit truncates AND
    pre-fills the composer via ``rpln_composer_prefill`` so the user can
    modify before resending. Copy goes through the clipboard API.

    ``timestamp_display`` is rendered at the left of the rail (Claude reference
    shows the timestamp co-located with the rail, not above it). Passing empty
    string omits the timestamp — caller decides whether the rail owns it or if
    the existing ``<p class="msg-ts">`` line above still renders it.
    """
    ts_html = f'<span class="rpln-rail-ts">{timestamp_display}</span>' if timestamp_display else ""
    st.markdown(
        f'<div class="rpln-user-rail" data-rpln-msg-idx="{idx}">'
        f"{ts_html}"
        f'<button class="rpln-rail-btn" data-rpln-action="retry-user" title="Retry">{_SVG_RETRY}</button>'
        f'<button class="rpln-rail-btn" data-rpln-action="edit-user" title="Edit">{_SVG_EDIT}</button>'
        f'<button class="rpln-rail-btn" data-rpln-action="copy-user" title="Copy">{_SVG_COPY}</button>'
        "</div>",
        unsafe_allow_html=True,
    )
    # Hidden bridges — JS dispatches clicks here, Streamlit reruns with the
    # callback effects below. Guarded against mid-stream clicks to avoid
    # clobbering state while the streamer is still writing.
    pending_stream = bool(st.session_state.get("rpln_pending_stream"))
    if (
        st.button("↺", key=f"rpln_regen_user_retry_{idx}", help="Retry this message")
        and not pending_stream
    ):
        messages = st.session_state.messages
        if 0 <= idx < len(messages) and messages[idx]["role"] == "user":
            question = messages[idx]["content"]
            st.session_state.messages = list(messages[:idx])
            st.session_state.messages_meta = {
                k: v for k, v in st.session_state.messages_meta.items() if k < idx
            }
            st.session_state.pending_question = question
            st.rerun()
    if (
        st.button("✎", key=f"rpln_regen_user_edit_{idx}", help="Edit this message")
        and not pending_stream
    ):
        messages = st.session_state.messages
        if 0 <= idx < len(messages) and messages[idx]["role"] == "user":
            st.session_state.rpln_composer_prefill = messages[idx]["content"]
            st.session_state.messages = list(messages[:idx])
            st.session_state.messages_meta = {
                k: v for k, v in st.session_state.messages_meta.items() if k < idx
            }
            # WP-F.05.09b.4 \u2014 flag the next user submission as an edit
            # so the user-bubble renderer can surface an "edited" badge.
            st.session_state.rpln_pending_edit = True
            st.rerun()


# ---------------------------------------------------------------------------
# Message content rendering
# ---------------------------------------------------------------------------


def _render_message_content(
    content: str,
    *,
    msg_idx: int = 0,
    tools_used: list[dict[str, str]] | None = None,
    _from_narrative: bool = False,
    _role: str = "assistant",
) -> None:
    """Render message content: figures as images, Python code collapsed, output visible.

    Python code blocks (```python ... ```) are hidden inside a click-to-expand
    section so the user sees analysis output immediately without wading through code.
    All other markdown and output blocks are rendered inline.
    """
    # Extract and render thinking blocks (Qwen /think, Claude thinking)
    think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL)
    think_matches = think_pattern.findall(content)
    if think_matches:
        thinking_text = "\n\n".join(m.strip() for m in think_matches)
        _render_thinking_block(thinking_text, msg_idx=msg_idx, tools_used=tools_used)
        # Remove thinking blocks from content for main display
        content = think_pattern.sub("", content).strip()

    # Render full analysis results directly from disk (bypasses LLM context limits)
    analysis_pattern = re.compile(r"<RPLN_ANALYSIS:([^>]+)>")
    analysis_match = analysis_pattern.search(content)
    if analysis_match:
        analysis_path = Path(analysis_match.group(1).strip())
        # Zone guard: only read files inside the agent zone (trio_bundle + agent/).
        # Matches the unified chokepoint used by every agent tool.
        try:
            validate_agent_read(analysis_path)
        except PermissionError as _perm_exc:
            # Show a debuggable card: path + reason + permitted zone.
            st.error(
                "**Analysis result blocked by zone guard.**\n\n"
                "The analysis tool saved results outside the permitted agent zone. "
                "This is a configuration error — contact your system administrator.",
                icon="🔒",
            )
            logger.error(
                "Zone guard blocked analysis read: path=%s reason=%s", analysis_path, _perm_exc
            )
            return
        if analysis_path.exists():
            # Replace the marker in content with just a brief note
            content = analysis_pattern.sub("", content).strip()
            # Render the full narrative from the saved file
            full_narrative = analysis_path.read_text(encoding="utf-8")
            # Recursively render the full narrative (which contains RPLN_FIGURE markers)
            _render_message_content(
                full_narrative,
                msg_idx=msg_idx,
                _from_narrative=True,
            )
            if content:
                st.markdown(_sanitize_file_refs(content))
            return
        else:
            st.warning(
                "**Analysis result not found.**  \n"
                "The analysis result was not saved to disk — the tool may have failed silently. "
                "Try running the query again.",
                icon="📂",
            )
            logger.warning("Analysis file missing: path=%s", analysis_path)
            content = analysis_pattern.sub("", content).strip()

    # Fallback: if content looks like an analysis response but the LLM
    # paraphrased the tool output and dropped the RPLN_ANALYSIS marker,
    # auto-render the narrative from disk.
    if not analysis_match and not _from_narrative and _role == "assistant":
        _fallback_cohort_ids: list[str] = []
        if re.search(r"cohort.?a|index.cases", content, re.IGNORECASE):
            _fallback_cohort_ids.append("cohort_a")
        if re.search(r"cohort.?b|household.contacts", content, re.IGNORECASE):
            _fallback_cohort_ids.append("cohort_b")

        for _cid in _fallback_cohort_ids:
            _narrative_path = config.STUDY_OUTPUT_DIR / "analysis" / f"{_cid}_narrative.md"
            _render_key = f"_narrative_rendered_{msg_idx}_{_cid}"
            if _narrative_path.exists() and _render_key not in st.session_state:
                st.session_state[_render_key] = True
                with st.expander(
                    f"\U0001f4ca Full Analysis Report \u2014 {_cid.replace('_', ' ').title()}",
                    expanded=True,
                ):
                    _full_narrative = _narrative_path.read_text(encoding="utf-8")
                    _render_message_content(
                        _full_narrative,
                        msg_idx=msg_idx * 1000 + hash(_cid) % 1000,
                        _from_narrative=True,
                    )

    # Split content on all artifact markers — figures (Plotly JSON, matplotlib
    # PNG) and saved analysis code (.py file from the sandbox).
    artifact_pattern = re.compile(r"<RPLN_(?:FIGURE|PLOTLY|CODE):([^>]+)>")
    python_block_re = re.compile(r"```python\n(.*?)```", re.DOTALL)

    marker_re = re.compile(r"<RPLN_(FIGURE|PLOTLY|CODE):([^>]+)>")
    markers: list[tuple[str, str]] = marker_re.findall(content)
    segments = artifact_pattern.split(content)
    marker_idx = 0

    for i, seg in enumerate(segments):
        if i % 2 == 0:
            # Text segment — collapse python blocks, render tables, render rest
            parts = python_block_re.split(seg)
            for j, part in enumerate(parts):
                if j % 2 == 0:
                    text = _sanitize_file_refs(part.strip())
                    if text:
                        remaining = _render_analysis_tables(text, key_prefix=f"{msg_idx}_{i}_{j}")
                        if remaining.strip():
                            st.markdown(remaining)
                else:
                    with st.expander("⟨/⟩ View code", expanded=False, key=f"code_{msg_idx}_{j}"):
                        st.code(part.strip(), language="python")
        else:
            artifact_path = Path(seg.strip())
            kind = markers[marker_idx][0] if marker_idx < len(markers) else "FIGURE"
            marker_idx += 1

            if kind == "CODE":
                _render_saved_code(artifact_path, msg_idx=msg_idx, code_idx=marker_idx)
            elif kind == "PLOTLY" and artifact_path.exists():
                _render_plotly_figure(artifact_path, msg_idx=msg_idx, fig_idx=marker_idx)
            elif artifact_path.exists():
                data, file_name, mime = _artifact_file_download(
                    artifact_path,
                    kind=kind,
                    index=marker_idx,
                )
                _render_artifact_bar(
                    key=f"{msg_idx}_{marker_idx}_figure",
                    data=data,
                    file_name=file_name,
                    mime=mime,
                )
                st.image(str(artifact_path), width="stretch")
            else:
                st.warning(
                    "**Figure file not found.**  \n"
                    "The plot was not saved to disk. Try running the query again.",
                    icon="🖼",
                )
                logger.warning("Figure file missing: path=%s", artifact_path)


def _render_saved_code(path: Path, *, msg_idx: int, code_idx: int) -> None:
    """Render a saved analysis ``.py`` file with copy-friendly code block + download.

    The user can read the code in-place, copy it from the rendered block, or
    download the ``.py`` file (which already includes a docstring header
    describing how to replicate the run via
    ``python -m scripts.ai_assistant.sandbox.replicate <file>``).
    """
    if not path.exists():
        st.caption(f"⟨/⟩ Saved code not found: `{path.name}` (cleanup may have removed it).")
        return
    text = path.read_text(encoding="utf-8")
    label = f"⟨/⟩ Generated code — `{path.name}` (click to expand)"
    with st.expander(label, expanded=False):
        st.caption(
            "This is the exact Python the agent ran. Copy it from the block "
            "below, or download the `.py` file — the header explains how to "
            "replicate the run on your own machine."
        )
        st.code(text, language="python")
        st.download_button(
            "Download .py",
            data=text.encode("utf-8"),
            file_name=path.name,
            mime="text/x-python",
            key=f"rpln_code_dl_{msg_idx}_{code_idx}",
        )


# ---------------------------------------------------------------------------
# Conversation export helpers
# ---------------------------------------------------------------------------


def _export_as_json() -> str:
    """Serialise the current conversation to JSON."""
    messages = st.session_state.messages
    meta = st.session_state.messages_meta
    export: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        entry: dict[str, Any] = {
            "index": i,
            "role": msg["role"],
            "content": redact_message_content(msg)["content"],
        }
        if i in meta:
            entry["timestamp"] = meta[i].get("timestamp", "")
            if msg["role"] == "assistant":
                entry["tools_used"] = [t["name"] for t in meta[i].get("tools_used", [])]
        export.append(entry)
    return json.dumps(
        {
            "study": config.STUDY_NAME,
            "thread_id": st.session_state.thread_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "messages": export,
        },
        indent=2,
        ensure_ascii=False,
    )


def _export_as_markdown() -> str:
    """Serialise the current conversation to Markdown."""
    messages = st.session_state.messages
    meta = st.session_state.messages_meta
    lines = [
        f"# RePORT AI Portal Conversation — {config.STUDY_NAME}",
        f"\n*Exported: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}*",
        f"*Thread: `{st.session_state.thread_id}`*\n",
        "---\n",
    ]
    for i, msg in enumerate(messages):
        ts = meta.get(i, {}).get("timestamp", "")
        ts_str = f"  <sub>{ts}</sub>" if ts else ""
        content = redact_message_content(msg)["content"]
        if msg["role"] == "user":
            lines.append(f"**You**{ts_str}\n\n{content}\n")
        else:
            tools = [t["name"] for t in meta.get(i, {}).get("tools_used", [])]
            tool_str = f"\n\n*Tools: {', '.join(f'`{t}`' for t in tools)}*" if tools else ""
            clean = _strip_internal_markers(content)
            lines.append(f"**RePORT AI Portal**{ts_str}\n\n{clean}{tool_str}\n")
        lines.append("---\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chat history rendering
# ---------------------------------------------------------------------------


def _render_chat_history() -> None:
    """Render all previous messages with figures, timestamps, and tool disclosures."""
    meta_map: dict[int, Any] = st.session_state.get("messages_meta", {})
    st.markdown(_CSS_MSG_ACTIONS, unsafe_allow_html=True)
    # st.iframe keeps the isolated execution model for the injected bridge
    # script while avoiding the legacy custom-components HTML path.
    with st.container(key="rpln_ui_bridge_streaming"):
        st.iframe(_JS_MSG_ACTIONS, width="content", height="content", tab_index=-1)
    for i, msg in enumerate(st.session_state.messages):
        avatar = "🔬" if msg["role"] == "assistant" else "👤"
        with st.chat_message(msg["role"], avatar=avatar):
            # WP-F.04.18e — pass persisted tools_used so the reasoning chip
            # label can switch to "Researching" on replay, matching the live
            # render's semantics.
            _replay_tools_used = (
                meta_map.get(i, {}).get("tools_used") if msg["role"] == "assistant" else None
            )
            _render_message_content(
                msg["content"],
                msg_idx=i,
                tools_used=_replay_tools_used,
                _role=msg["role"],
            )
            if msg["role"] == "assistant":
                _render_analysis_code_cards(_replay_tools_used, msg_idx=i)
            m = meta_map.get(i, {})
            # Timestamp
            ts = m.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    ts_display = dt.strftime("%H:%M · %b %d")
                except ValueError:
                    ts_display = ts
                st.markdown(
                    f'<p class="msg-ts">{ts_display}</p>',
                    unsafe_allow_html=True,
                )
            # Tool disclosure for AI messages
            tools_used = m.get("tools_used", [])
            if msg["role"] == "assistant" and tools_used:
                # Render sources section
                sources = _extract_sources(tools_used)
                _render_sources(sources, msg_idx=i)
                # Collapsible tool call details
                badge_html = "".join(
                    f'<span class="tool-badge">🛠 {t["name"]}</span>' for t in tools_used
                )
                with st.expander(
                    f"🛠 {len(tools_used)} tool call{'s' if len(tools_used) != 1 else ''}",
                    expanded=False,
                ):
                    st.markdown(badge_html, unsafe_allow_html=True)
                    for t in tools_used:
                        preview = t.get("content_preview", "")
                        if preview:
                            clean = _sanitize_file_refs(preview[:200])
                            st.caption(f"`{t['name']}` → {clean}")
            if msg["role"] == "assistant":
                _render_message_actions(i)
            elif msg["role"] == "user":
                # WP-F.05.09b.4 \u2014 "edited" badge surfaces when Edit flow
                # lands a re-submitted message at this index.
                if m.get("edited"):
                    st.markdown(
                        '<span class="rpln-edited-badge">edited</span>',
                        unsafe_allow_html=True,
                    )
                # WP-F.04.18b user action rail — hover-reveal Retry/Edit/Copy.
                # The existing .msg-ts above remains as the primary timestamp;
                # passing "" keeps the rail icon-only for now.
                _render_user_message_actions(i, timestamp_display="")


# ---------------------------------------------------------------------------
# Streaming response
# ---------------------------------------------------------------------------


def _stream_response(question: str) -> tuple[str, list[dict[str, str]]]:
    """Stream the agent's answer with live tool-activity indicators and tool tracking.

    Renders directly into the current ``st.chat_message`` context.
    Returns the full final response string. Provider/tool failures are converted
    to user-facing error markdown so chat history can persist them after rerun.
    Also stores metadata (tools_used, timestamp) in session_state.messages_meta
    keyed by the message index of the assistant response.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    st.markdown(_CSS_MSG_ACTIONS, unsafe_allow_html=True)
    status = st.empty()
    placeholder = st.empty()
    final_content: str = ""  # type: ignore[no-redef]
    tools_seen: list[str] = []
    tools_detail: list[dict[str, str]] = []  # type: ignore[no-redef]
    analysis_code_by_call_id: dict[str, str] = {}
    thinking_markup = (
        '<div class="rpln-thinking" role="status" aria-live="polite" '
        'aria-label="Working on it...">'
        '  <span class="rpln-thinking-label">Working on it...</span>'
        '  <span class="rpln-thinking-dots" aria-hidden="true">'
        "    <span></span><span></span><span></span>"
        "  </span>"
        "</div>"
    )

    # WP-F.04.18d — pre-token loader: keep a visible searching state until
    # first answer text arrives, then replace it with the streaming preview.
    status.markdown(thinking_markup, unsafe_allow_html=True)

    try:
        for chunk in stream_query(question, thread_id=st.session_state.thread_id):
            for node_output in chunk.values():
                if not isinstance(node_output, dict):
                    continue
                for msg in node_output.get("messages", []):
                    if isinstance(msg, ToolMessage):
                        name = getattr(msg, "name", None) or "tool"
                        tool_call_id = str(getattr(msg, "tool_call_id", "") or "")
                        analysis_code = analysis_code_by_call_id.pop(tool_call_id, None)
                        if name not in tools_seen:
                            tools_seen.append(name)
                            content_str = str(msg.content or "")
                            _upsert_tool_detail(
                                tools_detail,
                                name=name,
                                content_preview=_sanitize_file_refs(content_str[:300]),
                                analysis_code=analysis_code,
                            )
                        elif analysis_code:
                            _upsert_tool_detail(
                                tools_detail,
                                name=name,
                                content_preview="",
                                analysis_code=analysis_code,
                            )
                        status.markdown(thinking_markup, unsafe_allow_html=True)
                    elif isinstance(msg, AIMessage) and msg.tool_calls:
                        analysis_code_by_call_id.update(_analysis_code_payloads(msg))
                        status.markdown(thinking_markup, unsafe_allow_html=True)
                    elif isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                        raw = msg.content
                        if isinstance(raw, list):
                            # Anthropic returns list content blocks for tool-use turns;
                            # extract only the text blocks to avoid Python repr in chat.
                            content = " ".join(
                                b.get("text", "")
                                for b in raw
                                if isinstance(b, dict) and b.get("type") == "text"
                            ).strip()
                        else:
                            content = str(raw)
                        if content:
                            # Strip thinking blocks and RPLN_FIGURE markers for streaming preview
                            preview = re.sub(
                                r"<think>.*?</think>", "", content, flags=re.DOTALL
                            ).strip()
                            preview = re.sub(
                                r"<RPLN_(?:FIGURE|PLOTLY|ANALYSIS):[^>]+>",
                                " [artifact] ",
                                preview,
                            ).strip()
                            preview = _sanitize_file_refs(preview)
                            final_content = content
                            status.markdown(thinking_markup, unsafe_allow_html=True)
                            if preview.count("```") % 2 == 0:
                                placeholder.markdown(
                                    preview + ' <span class="rpln-caret">▍</span>',
                                    unsafe_allow_html=True,
                                )
                            else:
                                placeholder.markdown(preview)

        status.empty()
        if final_content:
            # Clear placeholder and render with full figure support
            placeholder.empty()
            _render_message_content(
                final_content,
                msg_idx=len(st.session_state.messages),
                tools_used=tools_detail,
            )
            _render_analysis_code_cards(tools_detail, msg_idx=len(st.session_state.messages))
            # Render sources and tool call disclosure inline (so they appear
            # immediately after streaming without requiring a page rerun).
            if tools_detail:
                sources = _extract_sources(tools_detail)
                _render_sources(sources, msg_idx=len(st.session_state.messages))
                badge_html = "".join(
                    f'<span class="tool-badge">🛠 {t["name"]}</span>' for t in tools_detail
                )
                with st.expander(
                    f"🛠 {len(tools_detail)} tool call{'s' if len(tools_detail) != 1 else ''}",
                    expanded=False,
                ):
                    st.markdown(badge_html, unsafe_allow_html=True)
                    for t in tools_detail:
                        preview = t.get("content_preview", "")
                        if preview:
                            clean = _sanitize_file_refs(preview[:200])
                            st.caption(f"`{t['name']}` → {clean}")
        else:
            final_content = (
                "No results found. Try rephrasing, or ask about a specific form or variable name."
            )
            placeholder.markdown(f"*{final_content}*")

    except Exception as exc:
        import traceback as _tb

        status.empty()
        placeholder.empty()
        err_msg = redact_phi_in_text(str(exc))
        exc_type = type(exc).__name__
        full_tb = sanitise_traceback(_tb.format_exc())
        low = err_msg.lower()

        _auth_keywords = (
            "api key",
            "api_key",
            "authentication",
            "unauthorized",
            "401",
            "403",
            "permission",
            "invalid x-api-key",
            "could not resolve authentication",
        )
        _rate_keywords = ("rate limit", "429", "quota", "too many requests")
        _model_keywords = (
            "404",
            "model not found",
            "does not exist",
            "invalid model",
            "no such model",
        )
        _conn_keywords = (
            "connection",
            "refused",
            "connecterror",
            "cannot reach",
            "timeout",
            "timed out",
        )
        _provider_keywords = ("unable to infer", "model_provider", "specify model_provider")
        # LLM failed to initialise (package missing, bad config, import error)
        _init_keywords = ("failed to initialise llm", "failed to initialize llm")
        # Upstream HTTP errors that don't fit the above buckets
        _bad_request_keywords = ("400", "bad request", "invalid_request_error")
        _server_error_keywords = (
            "500",
            "529",
            "overloaded",
            "internal server error",
            "service_unavailable",
        )

        _error_md: str
        if any(kw in low for kw in _auth_keywords):
            _error_md = (
                "🔑 **Authentication failed.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                "Your API key was rejected. Click **⚙ Change Setup** to update it."
            )
            st.error(
                f"**Authentication failed** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`. "
                "Click **⚙ Change Setup** to update your API key.",
                icon="🔑",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _rate_keywords):
            _error_md = (
                "⏱ **Rate limit reached.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                "The API provider is throttling requests. Wait a moment and try again."
            )
            st.error(
                f"**Rate limit reached** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}` is temporarily throttling requests. "
                "Wait a moment and try again.",
                icon="⏱",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _model_keywords):
            _error_md = (
                "🔍 **Model not found.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                "The model name may be incorrect. Click **⚙ Change Setup** to choose a different model."
            )
            st.error(
                f"**Model not found** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}` — "
                "name may be incorrect. Click **⚙ Change Setup**.",
                icon="🔍",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _MEMORY_KEYWORDS):
            # NOTE: memory branch must precede _conn_keywords — the all-rungs-
            # fail RuntimeError from agent_graph._init_llm contains the word
            # "refused", which also matches the connection-refused keyword set.
            # Specificity-first keeps the 💾 card from being shadowed by 🔌.
            _error_md = (
                "💾 **Out of memory.**\n\n"
                f"Model: `{config.LLM_MODEL}`\n\n"
                "The Ollama host refused to load the model — not enough free RAM. "
                "Close some apps, or click **⚙ Change Setup** to pick a smaller model "
                f"(e.g. `{config.QWEN3_DOWNGRADE_LADDER[-1]}`)."
            )
            st.error(
                f"**Out of memory** (`{exc_type}`). "
                f"Model: `{config.LLM_MODEL}` — not enough free RAM to load. "
                "Close apps to free RAM, or click **⚙ Change Setup** for a smaller model "
                f"(e.g. `{config.QWEN3_DOWNGRADE_LADDER[-1]}`).",
                icon="💾",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _conn_keywords):
            model_name = config.LLM_MODEL
            _error_md = (
                "🔌 **Cannot reach the LLM server.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                "If using Ollama, make sure it is running: `ollama serve`."
            )
            st.error(
                f"**Cannot reach the LLM server** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}`. "
                "If using Ollama, run:\n\n"
                f"```\nollama serve\nollama pull {model_name}\n```",
                icon="🔌",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _provider_keywords):
            _error_md = (
                "⚙️ **LLM provider not configured.**\n\n"
                f"Model: `{config.LLM_MODEL}`\n\n"
                "Could not determine provider. Click **⚙ Change Setup** to select one."
            )
            st.error(
                f"**LLM provider not configured** (`{exc_type}`). "
                f"Could not determine provider for model `{config.LLM_MODEL}`. "
                "Click **⚙ Change Setup**.",
                icon="⚙️",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _init_keywords):
            _error_md = (
                "🔧 **LLM initialisation failed.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                f"```\n{err_msg[:400]}\n```\n\n"
                "Check that the provider package is installed and the API key is set. "
                "Click **⚙ Change Setup** to reconfigure."
            )
            st.error(
                f"**LLM initialisation failed** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`.",
                icon="🔧",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _bad_request_keywords):
            _error_md = (
                "🚫 **Bad request (400).**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                f"```\n{err_msg[:400]}\n```\n\n"
                "The request was rejected by the API. Try rephrasing, or check the model's "
                "context window and tool support."
            )
            st.error(
                f"**Bad request (400)** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}` rejected the request.",
                icon="🚫",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        elif any(kw in low for kw in _server_error_keywords):
            _error_md = (
                "🌐 **API server error.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                f"```\n{err_msg[:400]}\n```\n\n"
                "The API server returned an error. Try again in a moment."
            )
            st.error(
                f"**API server error** (`{exc_type}`). "
                f"Provider: `{config.LLM_PROVIDER}` returned a server error. Try again.",
                icon="🌐",
            )
            with st.expander("Error details", expanded=True):
                st.code(err_msg, language="")
        else:
            # Fallback: show the exception type and message directly so the user
            # can see exactly what failed without hunting through collapsed panels.
            short_msg = err_msg[:500]
            _error_md = (
                f"⚠️ **Query failed — `{exc_type}`.**\n\n"
                f"Provider: `{config.LLM_PROVIDER}` · Model: `{config.LLM_MODEL}`\n\n"
                f"```\n{short_msg}\n```\n\n"
                "Try rephrasing, or click **⚙ Change Setup** to reconfigure."
            )
            st.error(
                f"**Query failed (`{exc_type}`):** {short_msg[:300]}",
                icon="⚠️",
            )
            with st.expander("Full traceback", expanded=True):
                st.code(full_tb, language="python")
        st.session_state["_rpln_stream_error"] = True
        logger.exception("Agent streaming error for query: %.80s", redact_phi_in_text(question))
        return _error_md, tools_detail

    return final_content, tools_detail  # type: ignore[return-value]
