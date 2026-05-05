"""Disk-backed multi-conversation persistence (JSON files)."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

import config
from scripts.ai_assistant.agent_graph import reset_agent
from scripts.ai_assistant.phi_safe import redact_message_content, redact_phi_in_text

logger = logging.getLogger(__name__)

_FILE_REF_EXTENSIONS = "jsonl|json|pdf|png|csv|xlsx|md"
_EXPORT_ARTIFACT_MARKER_RE = re.compile(r"<RPLN_(?:FIGURE|PLOTLY|ANALYSIS|CODE):[^>\r\n]*>")
_ABSOLUTE_FILE_REF_RE = re.compile(
    rf"(?<![\w.-])/(?:[\w.-]+/)*[\w.-]+\.(?:{_FILE_REF_EXTENSIONS})\b"
)
_RELATIVE_FILE_REF_RE = re.compile(rf"\b(?:[\w.-]+/)+[\w.-]+\.(?:{_FILE_REF_EXTENSIONS})\b")
_FILE_EXTENSION_RE = re.compile(rf"\.(?:{_FILE_REF_EXTENSIONS})\b")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _conversations_dir() -> Path:
    """Return the conversations directory, creating it if needed."""
    d = config.CONVERSATIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative time like '2h ago', 'Yesterday', 'Apr 12'."""
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return ""
    now = datetime.now(UTC)
    # Ensure both are timezone-aware for comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    diff = now - dt
    if diff.total_seconds() < 60:
        return "just now"
    elif diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() / 60)}m ago"
    elif diff.total_seconds() < 86400:
        return f"{int(diff.total_seconds() / 3600)}h ago"
    elif diff.days == 1:
        return "Yesterday"
    elif diff.days < 7:
        return f"{diff.days}d ago"
    else:
        return dt.strftime("%b %d")


def _conversation_title(messages: list[dict[str, str]]) -> str:
    """Derive a title from the first user message, truncated to 50 chars."""
    for msg in messages:
        if msg.get("role") == "user":
            text = msg["content"].strip().replace("\n", " ")
            return text[:50] + ("..." if len(text) > 50 else "")
    return "New conversation"


def _save_conversation() -> None:
    """Save the current session state to its conversation JSON file."""
    conv_id = st.session_state.get("current_conversation_id")
    if not conv_id:
        return
    messages = st.session_state.get("messages", [])
    # Don't save empty conversations with no messages
    if not messages:
        return
    meta = st.session_state.get("messages_meta", {})
    # Convert int keys to strings for JSON serialisation
    meta_serialisable = {str(k): v for k, v in meta.items()}
    now = datetime.now(UTC).isoformat()
    fpath = _conversations_dir() / f"{conv_id}.json"
    # Load existing to preserve created_at and pinned state
    existing: dict[str, Any] = {}
    if fpath.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            existing = json.loads(fpath.read_text(encoding="utf-8"))
    # Titles are persisted to disk inside the agent zone; the first user
    # message can carry PHI, so derive the title from a redacted view of
    # the messages before any disk write.
    if existing.get("title"):
        title = existing["title"]
    else:
        redacted_for_title = [redact_message_content(msg) for msg in messages]
        title = _conversation_title(redacted_for_title)
    # Already-saved messages are already redacted on disk; only redact new ones.
    already_saved = existing.get("messages", [])
    new_messages = messages[len(already_saved) :]
    redacted_messages = already_saved + [redact_message_content(msg) for msg in new_messages]
    data = {
        "id": conv_id,
        "title": title,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "pinned": existing.get("pinned", False),
        "messages": redacted_messages,
        "messages_meta": meta_serialisable,
        "llm_provider": st.session_state.get("llm_provider_label", ""),
        "llm_model": st.session_state.get("llm_model", ""),
        # Cached flag for fast sidebar filtering of conversations that include
        # figures, Plotly charts, or analysis artifacts. Computed from both
        # message markers and messages_meta has_figure flags.
        "has_artifacts": _has_artifacts_in_messages(messages, meta_serialisable),
    }
    try:
        fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        # Conversations may contain redacted user prompts + tool returns.
        # Tighten file mode to owner-only so the file isn't world-readable
        # if process umask is the typical 0o022.
        fpath.chmod(0o600)
        st.session_state.current_conversation_title = title
    except OSError:
        logger.warning("Failed to save conversation %s", conv_id)


def _load_conversation(conv_id: str) -> None:
    """Load a conversation from disk into session state and reset the agent."""
    fpath = _conversations_dir() / f"{conv_id}.json"
    if not fpath.exists():
        return
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load conversation %s", conv_id)
        return
    st.session_state.current_conversation_id = data["id"]
    st.session_state.current_conversation_title = data.get("title") or "New conversation"
    st.session_state.thread_id = data["id"]
    st.session_state.messages = data.get("messages", [])
    # Restore int keys from string-serialised meta
    raw_meta = data.get("messages_meta", {})
    st.session_state.messages_meta = {int(k): v for k, v in raw_meta.items()}
    reset_agent()


def _list_conversations() -> list[dict[str, Any]]:
    """Return all conversations sorted: pinned first, then by updated_at desc."""
    conv_dir = _conversations_dir()
    convs: list[dict[str, Any]] = []
    for fpath in conv_dir.glob("*.json"):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        messages = data.get("messages", [])
        preview = ""
        for msg in messages:
            if msg.get("role") == "user":
                preview = msg["content"].strip().replace("\n", " ")[:80]
                break
        convs.append(
            {
                "id": data.get("id", fpath.stem),
                "title": data.get("title", "Untitled"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "pinned": data.get("pinned", False),
                "message_count": len(messages),
                "preview": preview,
                "has_artifacts": bool(data.get("has_artifacts", False)),
            }
        )
    convs.sort(
        key=lambda c: (not c["pinned"], c.get("updated_at", "") == "", c.get("updated_at", "")),
        reverse=False,
    )
    # Within pinned and unpinned groups, sort by updated_at descending
    pinned = sorted(
        [c for c in convs if c["pinned"]], key=lambda c: c.get("updated_at", ""), reverse=True
    )
    recent = sorted(
        [c for c in convs if not c["pinned"]], key=lambda c: c.get("updated_at", ""), reverse=True
    )
    return pinned + recent


def _list_conversations_bucketed(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    """Return conversations grouped into time buckets based on created_at.

    Bucket order (for display): pinned → today → yesterday → last7 → last30 → older.
    Pinned conversations are separated regardless of creation date.
    Within each bucket, conversations are sorted by updated_at descending.
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    last7_start = today_start - timedelta(days=7)
    last30_start = today_start - timedelta(days=30)

    buckets: dict[str, list[dict[str, Any]]] = {
        "pinned": [],
        "today": [],
        "yesterday": [],
        "last7": [],
        "last30": [],
        "older": [],
    }

    for conv in _list_conversations()[:limit]:
        if conv["pinned"]:
            buckets["pinned"].append(conv)
            continue
        created_str = conv.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            buckets["older"].append(conv)
            continue
        if dt >= today_start:
            buckets["today"].append(conv)
        elif dt >= yesterday_start:
            buckets["yesterday"].append(conv)
        elif dt >= last7_start:
            buckets["last7"].append(conv)
        elif dt >= last30_start:
            buckets["last30"].append(conv)
        else:
            buckets["older"].append(conv)

    return buckets


def _delete_conversation(conv_id: str) -> None:
    """Delete a conversation JSON file."""
    fpath = _conversations_dir() / f"{conv_id}.json"
    if fpath.exists():
        fpath.unlink()


def _rename_conversation(conv_id: str, new_title: str) -> None:
    """Update the title in a conversation file."""
    fpath = _conversations_dir() / f"{conv_id}.json"
    if not fpath.exists():
        return
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        # User-supplied titles can carry PHI; redact before persisting to disk
        # in the agent zone (matches _save_conversation policy).
        data["title"] = redact_phi_in_text(new_title.strip())[:100]
        fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        fpath.chmod(0o600)
        if st.session_state.get("current_conversation_id") == conv_id:
            st.session_state.current_conversation_title = data["title"]
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to rename conversation %s", conv_id)


def _toggle_pin(conv_id: str) -> None:
    """Toggle the pinned field in a conversation file."""
    fpath = _conversations_dir() / f"{conv_id}.json"
    if not fpath.exists():
        return
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        data["pinned"] = not data.get("pinned", False)
        data["updated_at"] = datetime.now(UTC).isoformat()
        fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        fpath.chmod(0o600)
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to toggle pin for conversation %s", conv_id)


def _search_conversations(query: str) -> list[dict[str, Any]]:
    """Search conversations by title and message content (case-insensitive)."""
    if not query.strip():
        return _list_conversations()
    q = query.lower().strip()
    results: list[dict[str, Any]] = []
    conv_dir = _conversations_dir()
    for fpath in conv_dir.glob("*.json"):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        title = data.get("title", "").lower()
        if q in title:
            messages = data.get("messages", [])
            preview = ""
            for msg in messages:
                if msg.get("role") == "user":
                    preview = msg["content"].strip().replace("\n", " ")[:80]
                    break
            results.append(
                {
                    "id": data.get("id", fpath.stem),
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "pinned": data.get("pinned", False),
                    "message_count": len(messages),
                    "preview": preview,
                }
            )
            continue
        # Search message content
        for msg in data.get("messages", []):
            if q in msg.get("content", "").lower():
                messages = data.get("messages", [])
                preview = ""
                for m in messages:
                    if m.get("role") == "user":
                        preview = m["content"].strip().replace("\n", " ")[:80]
                        break
                results.append(
                    {
                        "id": data.get("id", fpath.stem),
                        "title": data.get("title", "Untitled"),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "pinned": data.get("pinned", False),
                        "message_count": len(messages),
                        "preview": preview,
                    }
                )
                break
    # Sort: pinned first, then by updated_at desc
    pinned = sorted(
        [c for c in results if c["pinned"]], key=lambda c: c.get("updated_at", ""), reverse=True
    )
    recent = sorted(
        [c for c in results if not c["pinned"]], key=lambda c: c.get("updated_at", ""), reverse=True
    )
    return pinned + recent


def _sanitize_export_content(content: str) -> str:
    """Strip internal artifact paths from downloaded conversation text."""
    content = _EXPORT_ARTIFACT_MARKER_RE.sub("[Artifact]", content)
    content = _ABSOLUTE_FILE_REF_RE.sub("", content)
    content = _RELATIVE_FILE_REF_RE.sub("", content)
    content = _FILE_EXTENSION_RE.sub("", content)
    return _EXCESS_BLANK_LINES_RE.sub("\n\n", content).strip()


def _export_conversation_as_text(conv_id: str) -> str:
    """Export a specific conversation as plain text."""
    fpath = _conversations_dir() / f"{conv_id}.json"
    if not fpath.exists():
        return ""
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    lines = [
        f"RePORT AI Portal Conversation — {config.STUDY_NAME}",
        f"Title: {data.get('title', 'Untitled')}",
        f"Exported: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "=" * 60,
        "",
    ]
    for msg in data.get("messages", []):
        role = "You" if msg.get("role") == "user" else "RePORT AI Portal"
        content = msg.get("content", "")
        content = (
            _EXPORT_ARTIFACT_MARKER_RE.sub("[Artifact]", content)
            if msg.get("role") == "user"
            else _sanitize_export_content(content)
        )
        lines.append(f"{role}:")
        lines.append(content)
        lines.append("")
        lines.append("-" * 40)
        lines.append("")
    return "\n".join(lines)


def _export_conversation_as_md(conv_id: str) -> str:
    """Export a specific conversation as Markdown."""
    fpath = _conversations_dir() / f"{conv_id}.json"
    if not fpath.exists():
        return ""
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    lines = [
        f"# RePORT AI Portal Conversation — {config.STUDY_NAME}",
        f"\n*Title: {data.get('title', 'Untitled')}*",
        f"*Exported: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}*\n",
        "---\n",
    ]
    for msg in data.get("messages", []):
        if msg.get("role") == "user":
            lines.append(f"**You**\n\n{msg.get('content', '')}\n")
        else:
            content = _sanitize_export_content(msg.get("content", ""))
            lines.append(f"**RePORT AI Portal**\n\n{content}\n")
        lines.append("---\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Artifact detection & export helpers
# ---------------------------------------------------------------------------

# Marker regexes — match streaming.py exactly.
_FIGURE_MARKER_RE = re.compile(r"<RPLN_FIGURE:([^>]+)>")
_PLOTLY_MARKER_RE = re.compile(r"<RPLN_PLOTLY:([^>]+)>")
_ANALYSIS_MARKER_RE = re.compile(r"<RPLN_ANALYSIS:([^>]+)>")


def _has_artifacts_in_messages(
    messages: list[dict[str, Any]],
    messages_meta: dict[str, Any] | None = None,
) -> bool:
    """Primitive check: scan an in-memory message list for any artifact marker.

    Also returns True if any ``messages_meta`` entry has ``has_figure=True``.
    """
    try:
        for msg in messages or []:
            content = msg.get("content", "") or ""
            if not isinstance(content, str):
                continue
            if (
                "<RPLN_FIGURE:" in content
                or "<RPLN_PLOTLY:" in content
                or "<RPLN_ANALYSIS:" in content
            ):
                return True
        if messages_meta:
            for meta_entry in messages_meta.values():
                if isinstance(meta_entry, dict) and meta_entry.get("has_figure"):
                    return True
    except (AttributeError, TypeError):
        return False
    return False


def _conversation_has_artifacts(conv_id: str) -> bool:
    """Return True if the conversation JSON contains any artifact marker.

    Loads the conversation from disk and delegates to the in-memory primitive.
    Returns False on any I/O or JSON decode error.
    """
    fpath = _conversations_dir() / f"{conv_id}.json"
    try:
        if not fpath.exists():
            return False
        data = json.loads(fpath.read_text(encoding="utf-8"))
        # Prefer cached flag when available for speed.
        if "has_artifacts" in data:
            cached = bool(data.get("has_artifacts"))
            if cached:
                return True
            # Fall through to a real scan so stale caches (pre-flag saves) still
            # work correctly the first time they are inspected.
        messages = data.get("messages", [])
        meta = data.get("messages_meta", {})
        return _has_artifacts_in_messages(messages, meta)
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _list_artifact_conversations() -> list[dict[str, Any]]:
    """Return the subset of :func:`_list_conversations` that has artifacts."""
    return [c for c in _list_conversations() if _conversation_has_artifacts(c["id"])]


def _extract_artifact_refs(conv_id: str) -> dict[str, list[str]]:
    """Return paths referenced by artifact markers in a conversation.

    Shape: ``{"figures": [...], "plotly": [...], "analyses": [...]}``.
    Empty lists on any failure; duplicates preserved (caller may dedupe).
    """
    result: dict[str, list[str]] = {"figures": [], "plotly": [], "analyses": []}
    fpath = _conversations_dir() / f"{conv_id}.json"
    try:
        if not fpath.exists():
            return result
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return result
    for msg in data.get("messages", []):
        content = msg.get("content", "") or ""
        if not isinstance(content, str):
            continue
        for m in _FIGURE_MARKER_RE.finditer(content):
            result["figures"].append(m.group(1).strip())
        for m in _PLOTLY_MARKER_RE.finditer(content):
            result["plotly"].append(m.group(1).strip())
        for m in _ANALYSIS_MARKER_RE.finditer(content):
            result["analyses"].append(m.group(1).strip())
    return result


def _export_plots_as_zip(conv_id: str, fmt: str) -> bytes:
    """Return a zip of all figures in a conversation, rendered as PNG/JPEG.

    - ``<RPLN_FIGURE:...>`` entries are PNG files on disk. When ``fmt='jpeg'`` we
      convert via Pillow (RGBA -> RGB) before re-encoding; PNGs are included
      verbatim when ``fmt='png'``.
    - ``<RPLN_PLOTLY:...>`` entries are JSON figure specs rendered via
      ``plotly.io.to_image`` (needs ``kaleido``). If kaleido is missing the
      plotly items are skipped and a ``README.txt`` in the zip notes the
      limitation.

    Returns ``b""`` if nothing could be added.
    """
    import zipfile
    from io import BytesIO

    fmt = (fmt or "png").lower()
    if fmt not in {"png", "jpeg"}:
        fmt = "png"

    refs = _extract_artifact_refs(conv_id)

    # Recursively pull figures referenced inside analysis .md narratives too —
    # analysis files frequently embed RPLN_FIGURE/RPLN_PLOTLY markers.
    for analysis_path_str in refs["analyses"]:
        try:
            ap = Path(analysis_path_str)
            if ap.exists():
                narrative = ap.read_text(encoding="utf-8")
                refs["figures"].extend(
                    m.group(1).strip() for m in _FIGURE_MARKER_RE.finditer(narrative)
                )
                refs["plotly"].extend(
                    m.group(1).strip() for m in _PLOTLY_MARKER_RE.finditer(narrative)
                )
        except OSError:
            continue

    # Dedupe while preserving order.
    figures = list(dict.fromkeys(refs["figures"]))
    plotly_paths = list(dict.fromkeys(refs["plotly"]))

    # Optional imports.
    try:
        from PIL import Image

        _HAS_PIL = True  # noqa: N806
    except ImportError:
        Image = None  # type: ignore[assignment]  # noqa: N806
        _HAS_PIL = False  # noqa: N806

    try:
        import plotly.io as pio

        _HAS_PLOTLY = True  # noqa: N806
    except ImportError:
        pio = None  # type: ignore[assignment]
        _HAS_PLOTLY = False  # noqa: N806

    _HAS_KALEIDO = importlib.util.find_spec("kaleido") is not None  # noqa: N806

    buf = BytesIO()
    added = 0
    notes: list[str] = []
    used_names: set[str] = set()

    def _unique(name: str) -> str:
        if name not in used_names:
            used_names.add(name)
            return name
        stem, _, ext = name.rpartition(".")
        i = 2
        while True:
            candidate = f"{stem}_{i}.{ext}" if stem else f"{name}_{i}"
            if candidate not in used_names:
                used_names.add(candidate)
                return candidate
            i += 1

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Figures (matplotlib PNGs on disk).
        for fig_str in figures:
            p = Path(fig_str)
            if not p.exists():
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if fmt == "png":
                arcname = _unique(p.stem + ".png")
                zf.writestr(arcname, raw)
                added += 1
            else:  # jpeg
                if _HAS_PIL and Image is not None:
                    try:
                        img = Image.open(BytesIO(raw))  # type: ignore[assignment]
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")  # type: ignore[assignment]
                        out = BytesIO()
                        img.save(out, format="JPEG", quality=92)
                        arcname = _unique(p.stem + ".jpeg")
                        zf.writestr(arcname, out.getvalue())
                        added += 1
                    except (OSError, ValueError):
                        # Fallback: include the original PNG if Pillow trips.
                        arcname = _unique(p.stem + ".png")
                        zf.writestr(arcname, raw)
                        added += 1
                else:
                    arcname = _unique(p.stem + ".png")
                    zf.writestr(arcname, raw)
                    added += 1
                    notes.append("Pillow not installed: figure(s) included as PNG instead of JPEG.")

        # Plotly figures (JSON specs) -> PNG/JPEG via kaleido.
        if plotly_paths:
            if _HAS_PLOTLY and _HAS_KALEIDO and pio is not None:
                for plt_str in plotly_paths:
                    p = Path(plt_str)
                    if not p.exists():
                        continue
                    try:
                        fig = pio.from_json(p.read_text(encoding="utf-8"))
                        img_bytes = pio.to_image(fig, format=fmt)
                    except Exception as _exc:
                        notes.append(f"Could not render {p.name} as {fmt.upper()}: {_exc!s}")
                        continue
                    arcname = _unique(p.stem + f".{fmt}")
                    zf.writestr(arcname, img_bytes)
                    added += 1
            else:
                notes.append(
                    f"Skipped {len(plotly_paths)} Plotly chart(s): kaleido is required to render "
                    "Plotly JSON to static images. Install with `uv add kaleido` "
                    "or `pip install kaleido`."
                )

        if notes:
            zf.writestr("README.txt", "\n".join(dict.fromkeys(notes)) + "\n")

    # Return the zip when either real assets were added OR we have a notes-only
    # README explaining *why* nothing was added (e.g. kaleido missing). When
    # there is neither content nor notes, return empty bytes so the caller can
    # suppress a download button.
    if added == 0 and not notes:
        return b""
    return buf.getvalue()


def _parse_markdown_tables(text: str) -> list[dict[str, Any]]:
    """Parse GFM-style markdown tables out of a text block.

    Returns a list of ``{"name": str, "columns": list[str], "rows": list[list[str]]}``.
    Names are inferred from the nearest preceding ``##``/``###`` heading, falling
    back to ``Table N``. Separator rows like ``|---|---|`` are ignored.
    """
    tables: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    table_idx = 0
    current_heading: str | None = None
    heading_re = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
    sep_re = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")

    while i < len(lines):
        line = lines[i]
        m = heading_re.match(line)
        if m:
            current_heading = m.group(1).strip()
            i += 1
            continue
        # A markdown table is: header row, then separator, then >=1 data rows.
        if "|" in line and i + 1 < len(lines) and sep_re.match(lines[i + 1]):
            header = [h.strip() for h in line.strip().strip("|").split("|")]
            i += 2
            data_rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                if sep_re.match(lines[i]):
                    i += 1
                    continue
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                # Pad/truncate to header width.
                if len(cells) < len(header):
                    cells = cells + [""] * (len(header) - len(cells))
                elif len(cells) > len(header):
                    cells = cells[: len(header)]
                data_rows.append(cells)
                i += 1
            if data_rows:
                table_idx += 1
                name = current_heading or f"Table {table_idx}"
                tables.append({"name": name, "columns": header, "rows": data_rows})
            continue
        i += 1

    return tables


def _export_tables_as_zip(conv_id: str, fmt: str) -> bytes:
    """Return a zip of tables extracted from analysis narratives.

    Analyses in this project are stored as Markdown narratives (``.md``) rather
    than structured JSON with a ``tables`` key, so we parse markdown tables
    out of the narrative. For each analysis:

    - ``fmt='csv'``: one ``.csv`` file per table.
    - ``fmt='xlsx'``: one ``.xlsx`` workbook per analysis, with one sheet per
      table (sheet names truncated to Excel's 31-char limit).

    Returns ``b""`` if nothing could be added.
    """
    import zipfile
    from io import BytesIO

    fmt = (fmt or "csv").lower()
    if fmt not in {"csv", "xlsx"}:
        fmt = "csv"

    refs = _extract_artifact_refs(conv_id)
    analysis_paths = list(dict.fromkeys(refs["analyses"]))
    if not analysis_paths:
        return b""

    try:
        import pandas as pd
    except ImportError:
        return b""

    buf = BytesIO()
    added = 0
    used_names: set[str] = set()

    def _unique(name: str) -> str:
        if name not in used_names:
            used_names.add(name)
            return name
        stem, _, ext = name.rpartition(".")
        i = 2
        while True:
            candidate = f"{stem}_{i}.{ext}" if stem else f"{name}_{i}"
            if candidate not in used_names:
                used_names.add(candidate)
                return candidate
            i += 1

    def _safe_stem(s: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
        return cleaned or "table"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ap_str in analysis_paths:
            ap = Path(ap_str)
            tables: list[dict[str, Any]] = []
            try:
                if ap.exists() and ap.suffix.lower() in {".md", ".markdown", ".txt"}:
                    tables = _parse_markdown_tables(ap.read_text(encoding="utf-8"))
                elif ap.exists() and ap.suffix.lower() == ".json":
                    # Defensive: also support the originally-specified JSON shape
                    # ``{"tables": [{"name","rows","columns"}, ...]}``.
                    data = json.loads(ap.read_text(encoding="utf-8"))
                    for t in data.get("tables", []) or []:
                        if not isinstance(t, dict):
                            continue
                        tables.append(
                            {
                                "name": str(t.get("name") or f"Table {len(tables) + 1}"),
                                "columns": list(t.get("columns") or []),
                                "rows": list(t.get("rows") or []),
                            }
                        )
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not tables:
                continue

            analysis_stem = _safe_stem(ap.stem)

            if fmt == "csv":
                for t_idx, t in enumerate(tables, start=1):
                    try:
                        df = pd.DataFrame(data=t["rows"], columns=t["columns"] or None)
                    except (ValueError, TypeError):
                        continue
                    tname = _safe_stem(t.get("name") or f"table_{t_idx}")
                    arcname = _unique(f"{analysis_stem}__{tname}.csv")
                    try:
                        zf.writestr(arcname, df.to_csv(index=False))
                        added += 1
                    except (OSError, ValueError):
                        continue
            else:  # xlsx — one workbook per analysis with one sheet per table
                xlsx_buf = BytesIO()
                try:
                    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
                        wrote = 0
                        used_sheet_names: set[str] = set()
                        for t_idx, t in enumerate(tables, start=1):
                            try:
                                df = pd.DataFrame(data=t["rows"], columns=t["columns"] or None)
                            except (ValueError, TypeError):
                                continue
                            raw_name = t.get("name") or f"Table {t_idx}"
                            sheet_name = re.sub(r"[\\/*?:\[\]]", "_", str(raw_name))[:31]
                            base_sheet = sheet_name or f"Table_{t_idx}"
                            candidate = base_sheet
                            j = 2
                            while candidate in used_sheet_names:
                                suffix = f"_{j}"
                                candidate = (base_sheet[: 31 - len(suffix)]) + suffix
                                j += 1
                            used_sheet_names.add(candidate)
                            df.to_excel(writer, sheet_name=candidate, index=False)
                            wrote += 1
                        if wrote == 0:
                            continue
                except (OSError, ValueError, ImportError):
                    continue
                arcname = _unique(f"{analysis_stem}.xlsx")
                zf.writestr(arcname, xlsx_buf.getvalue())
                added += 1

    if added == 0:
        return b""
    return buf.getvalue()
