"""Cross-platform OS-level resource limits for the sandbox subprocess.

Honest about platform asymmetry:

- **Linux:** ``RLIMIT_AS`` (memory), ``RLIMIT_NPROC`` (process count),
  ``RLIMIT_CPU`` (CPU time), and ``RLIMIT_NOFILE`` (file descriptors) all
  enforce reliably.
- **macOS:** ``RLIMIT_CPU`` and ``RLIMIT_NOFILE`` work; ``RLIMIT_DATA`` is set
  on best-effort but not strictly honored; ``RLIMIT_AS`` and ``RLIMIT_NPROC``
  are effectively no-ops on Darwin and we do not pretend otherwise.

The production deployment target is Linux. macOS is the developer environment;
the dev-vs-prod gap is documented in
``docs/sphinx/developer_guide/sandbox.rst``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable


def make_preexec_fn(
    *,
    cpu_seconds: int,
    memory_mb: int,
    max_procs: int,
    max_files: int,
) -> Callable[[], None] | None:
    """Build a ``preexec_fn`` for ``subprocess.Popen`` that applies rlimits in
    the child process immediately before the new program is launched.

    Returns ``None`` on Windows (where ``subprocess.Popen(preexec_fn=...)`` is
    not supported); the caller falls back to wall-clock-only protection there.
    """
    if sys.platform == "win32":
        return None

    def _apply() -> None:
        import contextlib
        import resource

        # Always-safe on Unix-like systems
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_NOFILE, (max_files, max_files))

        if sys.platform == "linux":
            # Strong on Linux: address-space cap reliably triggers OOM kill.
            with contextlib.suppress(ValueError, OSError):
                bytes_cap = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (bytes_cap, bytes_cap))
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(resource.RLIMIT_NPROC, (max_procs, max_procs))
        elif sys.platform == "darwin":
            # macOS: RLIMIT_AS is unreliable; RLIMIT_DATA is the best we can
            # do and it's still advisory. NOT a security boundary on Darwin.
            with contextlib.suppress(ValueError, OSError):
                bytes_cap = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_DATA, (bytes_cap, bytes_cap))

    return _apply
