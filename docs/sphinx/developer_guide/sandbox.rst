Sandbox: Subprocess-Isolated Code Execution
============================================

The agent's :func:`~scripts.ai_assistant.agent_tools.run_python_analysis`
tool runs LLM-generated Python in an OS-level isolated subprocess.  This
page documents what the sandbox protects against, what it deliberately
does *not* protect against, and how to tune it.

.. contents:: On this page
   :local:
   :depth: 2

Threat Model
------------

The sandbox is built for one specific risk: a hijacked agent prompt
emitting code that exfiltrates secrets or escapes the agent's narrow
output zone. Concretely the threat actor is the LLM itself (or a user
prompt-injecting it), not a remote network attacker.

**In-scope threats**

- Reading API keys (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY`` etc.)
  from ``os.environ``.
- Reading raw study data (``data/raw/``) or staged data (``tmp/``)
  outside the PHI-scrubbed trio bundle.
- Writing files anywhere outside ``output/{STUDY}/agent/analysis/``.
- Exhausting host resources via infinite loops, fork bombs, or huge
  memory allocations.
- Opening network sockets to exfiltrate data to a remote host.
- Bypassing the in-process AST guard via novel CPython gadgets.

**Out-of-scope (explicit non-goals)**

- Defending against an attacker with shell access on the host.
- Defending against malicious dependencies (``pandas``, ``numpy``,
  etc.) — those are trusted and pinned in ``pyproject.toml``.
- Side-channel attacks on the same machine.
- Protecting the user's own DataFrame contents from the user — the
  sandbox is a guard between the LLM and the host, not between the
  user and their own data.

Architecture
------------

The package ``scripts/ai_assistant/sandbox/`` has three components:

``__init__.py`` — the orchestrator
   Public API ``run_in_subprocess(code, ...)``. Builds a clean child
   environment (no ``*_API_KEY``, no ``PYTHONPATH`` from the parent),
   spawns the child via :func:`subprocess.run` with a wall-clock
   timeout, applies a ``preexec_fn`` from ``limits.py``, parses the
   child's JSON manifest, and validates that every figure / code
   path the manifest claims is actually inside ``output_dir``
   (defense against a malicious child fabricating manifest paths).

``runner.py`` — the child entry
   Invoked as ``python -m scripts.ai_assistant.sandbox.runner <spec>``.
   Carries the in-process AST/runtime guards (import allow-list,
   blocked-builtin call check, dunder filter on ``getattr`` /
   ``vars``, zone-guarded ``open``) — these run *inside* the
   subprocess, so even if process-level isolation has a flaw, the
   AST guards still bound what the code can do.  Loads pre-approved
   trio DataFrames from explicit paths in the spec (the runner does
   not import the project ``config`` module — its read/write zones
   come solely from the spec).

``limits.py`` — cross-platform rlimits
   ``make_preexec_fn`` sets ``RLIMIT_CPU`` (CPU time),
   ``RLIMIT_NOFILE`` (file descriptors), ``RLIMIT_AS`` (address
   space, Linux), and ``RLIMIT_NPROC`` (process count, Linux) before
   the child program is exec'd.

Defense in Depth
~~~~~~~~~~~~~~~~

Two layers, deliberately redundant:

1. **Subprocess isolation.** Clean env (no API keys), clean cwd,
   clean PYTHONPATH except for what the runner needs to import,
   OS-enforced resource limits, separate process boundary.
2. **AST + runtime guards inside the child.** Import allow-list,
   blocked-builtin AST check (``eval``, ``compile``, ``__import__``,
   etc.), dunder filter on ``getattr``/``vars``, zone-guarded
   ``builtins.open`` confined to ``output_dir`` for writes and the
   pre-loaded JSONL set + ``output_dir`` for reads.

Either layer alone would block the headline threat. Both together
make a CVE-class CPython escape needed to do real damage.

The macOS Asymmetry
-------------------

This is the load-bearing caveat:

- **Linux** is the production deployment target. ``RLIMIT_AS``,
  ``RLIMIT_NPROC``, ``RLIMIT_CPU``, and ``RLIMIT_NOFILE`` all
  enforce reliably. A 2 GB memory allocation hits ``RLIMIT_AS`` and
  the child dies. A fork bomb hits ``RLIMIT_NPROC``. A CPU spin
  loop hits ``RLIMIT_CPU`` and the child receives ``SIGXCPU``.
- **macOS** is the developer environment. ``RLIMIT_CPU`` and
  ``RLIMIT_NOFILE`` work. ``RLIMIT_DATA`` is set on best-effort but
  not strictly honored. ``RLIMIT_AS`` and ``RLIMIT_NPROC`` are
  effectively no-ops on Darwin and we do not pretend otherwise.

The CI pipeline runs on Ubuntu and exercises every test in
``tests/security/test_sandbox_isolation.py`` including the three
``@pytest.mark.skipif(sys.platform != "linux", ...)`` cases. On
macOS the same tests skip with a clear marker.  If you change the
sandbox, run the suite locally then verify the Linux-only tests
pass on CI before merging.

Configurable Knobs
------------------

Operational tunables live in ``config.py`` and are env-overridable.
They are *safe* in the sense that lowering any of them only tightens
the security envelope — none of these can weaken the trust boundary.

================================== ========= ==========================================
Setting                            Default   Effect
================================== ========= ==========================================
``ANALYSIS_TIMEOUT``               300 s     Wall-clock kill at this many seconds.
``ANALYSIS_MAX_OUTPUT``            200_000   Cap on captured stdout (bytes).
``ANALYSIS_MAX_FIGURES``           20        Cap on collected figures per run.
``SANDBOX_MAX_MEMORY_MB``          512       ``RLIMIT_AS`` cap on Linux.
``SANDBOX_MAX_PROCS``              64        ``RLIMIT_NPROC`` cap on Linux.
``SANDBOX_MAX_FILES``              64        ``RLIMIT_NOFILE`` cap.
``SANDBOX_PERSIST_CODE``           true      Save executed code as ``.py``.
================================== ========= ==========================================

What is *not* configurable from ``config.py`` (intentional):

- The import allow-list (``_ALLOWED_IMPORTS`` in ``runner.py``).
- The blocked builtins list (``_BLOCKED_BUILTINS``).
- The dunder filter list (``_BLOCKED_DUNDERS``).
- The env-var blocklist prefixes (``_BLOCKED_PREFIXES`` in
  ``__init__.py``).
- The env-var allow-list (``_SAFE_ENV_KEYS``).

Adding to any of those is a security-relevant change and must be a
code change reviewed in a PR — not a config flip.

Code Persistence and Replication
--------------------------------

When ``SANDBOX_PERSIST_CODE`` is true (default), every successful
sandbox run also saves the executed code as a ``.py`` file under
``output/{STUDY}/agent/analysis/code/run_<ISO_TIMESTAMP>_<UUID>.py``.
The file leads with a docstring header listing the pre-loaded
DataFrames and pointing the user at the replication helper:

.. code-block:: bash

   python -m scripts.ai_assistant.sandbox.replicate \
       output/{STUDY}/agent/analysis/code/run_2026-04-27T01-23-45Z_a1b2c3d4.py

The replication helper applies the same AST allow-list (defense in
depth on locally re-run code) and then executes the code in the
caller's current Python process — so the user can see output
unfiltered, write files to their working directory, and interact
with figures normally.

The Streamlit UI surfaces saved code through a new ``<RPLN_CODE:...>``
marker rendered as a collapsible code block plus a download button —
the user can copy the source from the rendered block or download the
``.py`` file directly.

Where the Code is *Not* Saved
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The agent's pre-execution rejections (AST guard, blocked import,
syntax error) do not produce a saved file — there's no useful code
to replicate. Same for runtime errors: the file is only written
after a successful run.

Tests
-----

``tests/security/test_sandbox_isolation.py`` covers the three
contracts:

- **Confidentiality** — env-var leak, blocked-prefix sweep, read-zone
  enforcement.
- **Integrity** — write-zone strictness, manifest-path traversal
  rejection, AST guard preservation.
- **Availability** — wall-clock timeout, ``RLIMIT_CPU`` /
  ``RLIMIT_AS`` / ``RLIMIT_NPROC`` (Linux), network-import blocked.

Plus legitimate-use tests proving the sandbox still does its day
job (pandas group-by, plotly JSON write, matplotlib PNG save) and
the new code-persistence tests (file written, marker emitted,
header includes the DataFrame names, persistence togglable).

Run them with:

.. code-block:: bash

   uv run pytest tests/security/test_sandbox_isolation.py -v

Total runtime is ~75 s on macOS (subprocess startup is the bottleneck).

Future Work
-----------

Out of scope for v0.17.0; tracked for later releases:

- Convert trio JSONL → parquet so the child loads DataFrames with
  ``mmap`` instead of re-parsing JSON on every call (~80 % of the
  per-call overhead).
- Add ``seccomp-bpf`` syscall filtering on Linux for stronger
  network-egress denial than the import allow-list alone.
- Add an opt-in ``nsjail``/``Docker`` profile for high-assurance
  deployments where even a CVE-class CPython escape is in scope.
- Add code-retention auto-cleanup based on
  ``SANDBOX_CODE_RETENTION_DAYS`` (currently kept indefinitely).

When You Touch This Code
------------------------

- **Adding a new allowed import** is a security change. Open a PR,
  document why the new module is safe to expose to LLM-generated
  code, and add a regression test that exercises it through the
  sandbox.
- **Loosening any of the rlimits** is a security change. Document
  the rationale in the PR description and the IRB conformance
  matrix if the change affects the agent boundary's posture.
- **Changing the env allow-list** is a security change. The default
  list (``PATH``, ``LANG``, ``LC_ALL``, ``TZ``, ``PYTHONPATH``) is
  the minimum the child needs to import its dependencies; adding
  anything risks leaking parent state into the child.
- **Tweaking timeouts or memory caps** is operational, not security:
  ``config.py`` is the right place. New env-var knobs go through
  ``_get_env_int`` so the env layer behaves identically to the YAML
  overlay.
