"""Subprocess-isolated execution for LLM-generated Python analysis code.

Threat model: the agent's prompt may be hijacked into emitting hostile code
(e.g. ``import os; print(os.environ['ANTHROPIC_API_KEY'])``). The in-process
AST/runtime guards in ``runner.py`` reject most such code, but a defense-in-
depth layer is provided by running the code in a fresh Python interpreter with
a clean environment (no ``*_API_KEY`` vars), OS-level resource limits
(``RLIMIT_*``), and a wall-clock timeout. Output is captured and post-processed
through ``phi_safe_return`` before reaching the LLM.

See ``docs/sphinx/developer_guide/sandbox.rst`` for the full discussion of
threats covered, threats not covered, and the macOS limitations.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ._result import SandboxResult
from .limits import make_preexec_fn

__all__ = ["SandboxResult", "run_in_subprocess"]


# Env vars the child is allowed to see. Everything else is stripped.
_SAFE_ENV_KEYS: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONPATH")

# Any env var name starting with one of these prefixes — or ending in
# ``_API_KEY`` — is unconditionally stripped, even if it would otherwise
# pass the allowlist. Defense in depth against future env-var additions.
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "OPENAI_",
    "GOOGLE_",
    "GEMINI_",
    "NVIDIA_",
    "AZURE_",
    "AWS_",
)


def _build_clean_env(tmpdir: Path, project_root: Path) -> dict[str, str]:
    """Construct the child's env: only allowlisted keys, never an API key."""
    env: dict[str, str] = {}
    for k in _SAFE_ENV_KEYS:
        if k in os.environ:
            env[k] = os.environ[k]
    # PYTHONPATH must include the project root so the child can find
    # ``scripts.ai_assistant.sandbox.runner`` via ``-m``.
    existing_pp = env.get("PYTHONPATH", "")
    parts = [str(project_root)] + ([existing_pp] if existing_pp else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["HOME"] = str(tmpdir)
    env["TMPDIR"] = str(tmpdir)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("LC_ALL", "C.UTF-8")
    # Final defensive sweep: drop anything that could leak credentials,
    # in case the allowlist above is ever extended carelessly.
    for k in list(env):
        if k.endswith("_API_KEY") or any(k.startswith(p) for p in _BLOCKED_PREFIXES):
            del env[k]
    return env


def _project_root() -> Path:
    """Locate the project root (where ``scripts/`` lives) for PYTHONPATH."""
    here = Path(__file__).resolve()
    # scripts/ai_assistant/sandbox/__init__.py → walk up 3 levels.
    return here.parent.parent.parent.parent


def _read_manifest(output_dir: Path) -> dict | None:
    manifest_path = output_dir / "_sandbox_result.json"
    if not manifest_path.exists():
        return None
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _validate_paths_inside(paths: list[str], parent: Path) -> list[Path]:
    """Reject any reported figure/code path that isn't actually inside parent.

    The child writes its own manifest, so a malicious child could claim
    paths it didn't actually create. Defense: verify containment in the parent.
    """
    parent_resolved = parent.resolve()
    out: list[Path] = []
    for p_str in paths:
        p = Path(p_str)
        try:
            p.resolve().relative_to(parent_resolved)
        except (ValueError, OSError):
            continue
        if p.exists():
            out.append(p)
    return out


def run_in_subprocess(
    code: str,
    *,
    df_paths: dict[str, str],
    output_dir: Path,
    timeout_s: int = 300,
    max_memory_mb: int = 512,
    max_procs: int = 64,
    max_files: int = 64,
    persist_code: bool = True,
    max_output_bytes: int = 200_000,
    max_figures: int = 20,
) -> SandboxResult:
    """Run ``code`` in an isolated subprocess; return a structured result.

    The child runs the in-process AST/runtime guards (defense in depth) and
    has a clean environment with no API keys. ``output_dir`` is the only
    writable area; the only readable inputs are the JSONL files in
    ``df_paths`` plus anything inside ``output_dir``.

    Wall-clock-bounded by ``timeout_s``. On Linux, additionally bounded by
    ``RLIMIT_AS`` (memory), ``RLIMIT_NPROC`` (process count), ``RLIMIT_CPU``
    (CPU time), ``RLIMIT_NOFILE`` (file descriptors). On macOS, ``RLIMIT_AS``
    and ``RLIMIT_NPROC`` are advisory only — see the module docstring.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    project_root = _project_root()

    with tempfile.TemporaryDirectory(prefix="rpln_sandbox_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        spec = {
            "code": code,
            "df_paths": df_paths,
            "output_dir": str(output_dir.resolve()),
            "persist_code": persist_code,
            "max_output_bytes": max_output_bytes,
            "max_figures": max_figures,
        }
        spec_path = tmpdir / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")

        env = _build_clean_env(tmpdir, project_root)
        # CPU rlimit: leave a small margin under wall-clock so RLIMIT_CPU
        # fires first if the code is genuinely CPU-bound.
        cpu_seconds = max(1, timeout_s - 1)
        preexec = make_preexec_fn(
            cpu_seconds=cpu_seconds,
            memory_mb=max_memory_mb,
            max_procs=max_procs,
            max_files=max_files,
        )

        proc_kwargs: dict = {
            "env": env,
            "cwd": str(tmpdir),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout_s + 5,  # outer guard; inner CPU rlimit is tighter
        }
        if preexec is not None:
            proc_kwargs["preexec_fn"] = preexec

        # Invoke the runner via its file path rather than ``-m`` so that the
        # child does NOT execute ``scripts/ai_assistant/__init__.py`` — that
        # chain imports langchain / langgraph / every LLM SDK and reserves
        # multi-GB of vmap before any user code runs, which trips RLIMIT_AS
        # on Linux at any reasonable cap. Direct path invocation gives the
        # child only what ``runner.py`` itself imports (stdlib + pandas/numpy
        # via lazy ``_load_dataframes``).
        runner_path = Path(__file__).parent / "runner.py"
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-I",
                    str(runner_path),
                    str(spec_path),
                ],
                **proc_kwargs,
            )
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or b"").decode("utf-8", errors="replace")
            stderr = (e.stderr or b"").decode("utf-8", errors="replace")
            return SandboxResult(
                stdout=stdout,
                stderr=stderr or "Wall-clock timeout exceeded.",
                exit_code=-1,
                timed_out=True,
            )

        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        exit_code = completed.returncode

        manifest = _read_manifest(output_dir)
        figure_paths: list[Path] = []
        code_paths: list[Path] = []
        truncated = False
        oom_killed = False
        timed_out = False
        if manifest is not None:
            figure_paths = _validate_paths_inside(manifest.get("figure_paths", []), output_dir)
            code_paths = _validate_paths_inside(manifest.get("code_paths", []), output_dir)
            truncated = bool(manifest.get("truncated", False))
        else:
            # No manifest typically means the OS killed the child before it
            # could write one. Map the negative signal codes to the right flag:
            #   -9 / 137 = SIGKILL  → most often the OOM killer on Linux
            #  -15 / 143 = SIGTERM  → external kill
            #  -24 / 152 = SIGXCPU  → RLIMIT_CPU exceeded
            #  -25 / 153 = SIGXFSZ  → RLIMIT_FSIZE exceeded
            if exit_code in (-24, 152):
                timed_out = True  # CPU time limit is effectively a timeout
            elif exit_code in (-9, 137):
                oom_killed = True  # SIGKILL is most often OOM on Linux
            elif exit_code in (-15, 143, -25, 153):
                # Other forced terminations: surface as timed_out for the
                # caller's convenience; exit_code preserves the detail.
                timed_out = True

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            figure_paths=figure_paths,
            code_paths=code_paths,
            truncated=truncated,
            oom_killed=oom_killed,
            timed_out=timed_out,
        )
