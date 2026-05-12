Architecture Decisions (ADRs)
=============================

**What.** One record per major architectural decision. ADRs 001–009
cover the original PHI-handling architecture. ADRs 010–015 cover the
sandbox, KeyStore, PDF orchestrator, reviewed snapshot baseline, parallel
extraction, and l-diversity decisions. Each record states
what was decided, why, how it was implemented, what alternatives were
considered, and what consequences to expect if the decision ages
poorly.

**Why.** Commit messages and Claude-memory files capture decisions at
the moment of authorship but are invisible to a new contributor who
starts with ``git clone``. An ADR page is the stable artifact: the next
person who asks "why did they choose rule+allowlist over Presidio?" can
find the answer here without spelunking ``git log``.

**How.** One section per decision. Section layout: **What** (the
decision stated plainly) · **Why** (the driving constraint or
evidence) · **How** (the implementation mechanism) · **Alternatives**
(what was considered and rejected, with the reason) · **Consequences**
(what to watch for if the decision needs revisiting).

.. contents:: On this page
   :local:
   :depth: 1

ADR-001 — Single-study, local-first runtime
-------------------------------------------

**What.** The pipeline processes one fixed study under
``data/raw/{STUDY_NAME}/`` per install. Multi-study, federated, HPC,
and cloud-deploy workflows are explicitly out of scope.

**Why.** Clinical-study privacy posture is per-study. Cross-study
workflows introduce cross-study re-identification risk that needs its
own threat model; that's a different project. Keeping this one narrow
lets every defence be tuned to one study's data dictionary and one
IRB's expectations.

**How.** ``config.STUDY_NAME`` pins the single study; every path under
``data/raw/``, ``tmp/``, and ``output/`` is parameterised by it.

**Alternatives.** An upload-driven multi-tenant variant was considered
at project start; rejected because multi-tenant isolation would have
dominated the architecture while the real demand was single-study
velocity.

**Consequences.** Supporting a second study today means a second repo
clone with a different ``STUDY_NAME``. If that becomes a burden, revisit
by first generalising ``config.py`` study resolution, not by hacking
new paths into the pipeline.

ADR-002 — HMAC-SHA256 pseudonymization with sidecar key (no vault)
------------------------------------------------------------------

**What.** Subject IDs are replaced with ``SUBJ_<hmac-sha256(key, id)[:12]>``.
The 32-byte key lives at ``~/.config/report_ai_portal/phi_key`` (mode
0600) outside the repo tree.

**Why.** HMAC-SHA256 is non-reversible without the key (so pseudonyms
leak no information about the identifier) and deterministic with the
key (so the same subject maps to the same tag across every file in the
bundle — longitudinal linkage preserved). A sidecar key outside the
repo tree means key compromise requires host-level access; a
repo-committed key would ride into every clone.

**How.** :func:`scripts.security.phi_scrub.pseudo_id` + :func:`scripts.security.phi_scrub.bootstrap_key`;
key permissions enforced by :func:`scripts.security.phi_scrub.load_key`
(hard-fail on missing or wrong mode).

**Alternatives.**

* **AES-256-GCM keyvault with a database of original→pseudonym mappings**
  (the vault.py approach, evaluated and rejected early in design). Rejected because
  a mapping database is a decryption surface — anyone with the key can
  invert every pseudonym. HMAC-only is strictly safer: deletion of the
  key forfeits the ability to re-derive pseudonyms, so key
  compartmentalisation is simpler to reason about.
* **Plain SHA-256 hashing**. Rejected — hashing without a secret key is
  vulnerable to rainbow-table attacks when the input space is small
  (subject IDs are typically 4-5 digits).
* **Random tokens stored in a database**. Rejected — breaks
  longitudinal linkage across studies or re-runs; same subject would
  get different tokens per run.

**Consequences.** Key rotation invalidates every prior pseudonym; full
re-ingestion from raw is required. This is the intentional behaviour
but can surprise new operators. Document explicitly in the key-
management runbook.

ADR-003 — SANT per-subject date jitter over HIPAA year-only
-----------------------------------------------------------

**What.** Event dates are shifted by a per-subject deterministic
offset ∈ [-30, +30] days. The offset is the same for every date
belonging to one subject, so intra-subject intervals are preserved
exactly.

**Why.** HIPAA Safe Harbor reduces dates to "year only" — that loses up
to 364 days of resolution and breaks survival / incidence / person-time
analyses. SANT jitter preserves per-subject interval structure (enough
for every epi question the agent is expected to answer) while still
obscuring the absolute calendar date.

**How.** :func:`scripts.security.phi_scrub.date_offset_days` derives
the offset from ``HMAC-SHA256(key, subject_id)[:4]`` mod (2N+1) − N.
:func:`scripts.security.phi_scrub.shift_date` applies it while preserving
the source format (ISO / M-D-Y / D-M-Y).

**Alternatives.**

* **Year-only masking** (strict Safe Harbor). Rejected because it
  breaks survival analysis and time-to-event fits — core to the study
  research questions.
* **Global constant offset across all subjects**. Rejected because the
  offset would be a single secret with no per-subject variation — one
  leaked event date reveals every subject's calendar events.
* **Per-row random offset**. Rejected because it destroys per-subject
  interval structure that the epi analyses depend on.

**Consequences.** SANT is a Limited Dataset technique in the HIPAA
taxonomy (not Safe Harbor). The ``compliance_posture`` flag in
``phi_scrub.yaml`` makes this explicit — ``safe_harbor`` drops DOB
entirely; ``limited_dataset`` requires an IRB-approved Data Use
Agreement file to exist before the scrub will shift DOB along with
other dates.

ADR-004 — Rule+allowlist over Microsoft Presidio
------------------------------------------------

**What.** The PHI gate and log redactor use a curated regex catalog
(:mod:`scripts.security.phi_patterns`) plus a clinical-phrase allowlist
(:mod:`scripts.security.phi_allowlist`). Presidio is NOT installed as a
runtime dependency.

**Why.** 2025 benchmarks (cited in :doc:`references`) found Presidio
runs 22.7% precision in mixed enterprise data and ~84% F1 on clinical
notes; curated rules on calibrated fields consistently outperform it.
The false-positive tax alone (legitimate clinical phrases flagged as
PHI) would make every agent response unusable.

**How.** ``phi_gate.py`` compiles ``phi_patterns.BLOCKING_PATTERNS`` +
``WARN_PATTERNS``; clinical-phrase allowlist suppresses warn-tier hits
like "Treatment Completed" that match the generic name-like heuristic.

**Alternatives.**

* **Presidio-analyzer default**. Rejected per the benchmarks above.
* **John Snow Labs Clinical NER (commercial, 98.6% F1)**. Rejected
  because it's commercial and requires a JSL license key; the
  architecture prioritises local-first zero-egress.
* **Stanford Stanza with i2b2-tuned model**. Considered; ~93% F1 open-
  source. Deferred as an optional supplement for narrative residuals
  if the rule catalog proves insufficient.
* **Local Ollama prompt-engineered NER**. Rejected for the current
  runtime because the calibrated rule catalog plus whole-field narrative
  drops provide a smaller, auditable surface.

**Consequences.** Rule maintenance cost scales with new PHI classes
discovered during deployment. Offset by the catalog being data-driven
(YAML + rule-class helpers) rather than inline regex — adding a new
PHI class is a YAML addition plus a test case, not a code change.

ADR-005 — tmpfs staging as an operator opt-in (not default)
-----------------------------------------------------------

**What.** When ``REPORTALIN_TMPFS_STAGING=1`` is set AND Linux
``/dev/shm`` is writable, the extraction leg redirects staging to
``/dev/shm/report_ai_portal/{STUDY}/`` so raw extracted rows never hit
physical disk. Default is off.

**Why.** tmpfs-backed staging is the gold-standard defence against
filesystem forensics on the extraction host, but forcing it on by
default would break macOS and Windows operators without warning. Opt-in
keeps the strong defence available to Linux operators who ask for it
while preserving portability for everyone else.

**How.** :func:`scripts.utils.secure_staging.resolve_staging_root`
gates tmpfs redirection on the env flag AND the writability check.
Graceful fallback to the default ``tmp/{STUDY}/`` path when the env
flag is set but the tmpfs isn't available.

**Alternatives.**

* **Encrypted-at-rest staging**. Considered; rejected as over-
  engineering — staging is short-lived, the on-disk hardening (mode 0700
  + umask 0077 + zero-fill teardown) already resists the realistic
  threat model (accidental disk imaging, forensic recovery). Adding
  AES at rest would add a key-management burden without closing a
  different attack surface.
* **Default-on tmpfs with a Windows/macOS fallback**. Considered;
  rejected because silent platform-dependent behaviour is a foot-gun —
  operators may assume tmpfs is active when it isn't. Explicit opt-in
  makes the posture auditable.

**Consequences.** Linux operators who forget the env flag run without
tmpfs hardening. Mitigated by documenting the flag prominently in
``.env.example``, :doc:`../user_guide/configuration`, and the
operational runbook.

ADR-006 — External-API PDF extraction refused by default
--------------------------------------------------------

**What.** ``scripts/extraction/extract_pdf_data._resolve_pdf_provider``
refuses to initialise an Anthropic / Google Gemini client unless the
operator explicitly sets ``REPORTALIN_PDF_PHI_FREE=1``.

**Why.** PDFs are treated as PHI-bearing by default — annotated CRFs
can carry filled-in patient data, handwritten signatures, or example
subject IDs in annotations. Sending them to an external LLM API is a
network egress of PHI to a third party. The architecture forbids
that without explicit operator assertion.

**How.** Feature-flag check at provider resolution; refusal raises
``ValueError`` with a remediation message listing three alternatives
(flip the flag if PHI-free, use ``--pdf-source`` with pre-extracted
JSON, skip the PDF leg entirely).

**Alternatives.**

* **Local-only PDF extraction** (pdfplumber primary + local-Ollama
  multimodal fallback). **Superseded by the PDF orchestrator:**
  ``scripts/extraction/pdf_pipeline.py`` ships pdfplumber as the
  always-on code path, paired with a redacted-text LLM call (capable
  cloud or local model) via ``_merge``, and a per-PDF snapshot
  baseline fallback at ``data/snapshots/{STUDY}/pdfs/``. No raw PDF bytes
  leave the host on the orchestrator path. The original ADR-006
  external-API gate remains as the legacy fallback for operators who
  cannot run the orchestrator.
* **Keep external-API default with an "I acknowledge PHI" banner**.
  Rejected — banners are not operator assertions. An env flag creates
  a durable audit trail.
* **Drop PDF extraction entirely**. Considered as the minimum safe
  posture; rejected because the annotated PDFs carry variable-definition
  annotations that the data dictionary alone does not cover.

**Consequences.** A new operator who needs PDF extraction must read the
error message, verify their PDFs are PHI-free, and flip the flag.
This is the intentional friction.

ADR-007 — Four-tier architecture (RED / AMBER / GREEN / GREEN-PROTECT)
----------------------------------------------------------------------

**What.** Every path in the runtime belongs to exactly one of four
named zones: RED (raw), AMBER (staging), GREEN (trio_bundle), or
GREEN-PROTECT (agent boundary). The zones are enforced by assertions,
not by convention.

**Why.** Convention-based zone separation rots; assertion-based zone
separation fails fast. New code that accidentally reads from raw/ or
writes to output/ before the scrub runs raises ``ZoneViolationError``
in CI before landing.

**How.** :mod:`scripts.security.secure_env` exposes the pipeline-side
guards (``assert_not_raw``, ``assert_write_zone``, ``assert_output_zone``,
``assert_trio_bundle_zone``) — one per tier. The agent world has its own
chokepoint :mod:`scripts.ai_assistant.file_access` with
``validate_agent_read`` / ``validate_agent_write`` /
``validate_sandbox_write`` (layered on the same ``ZoneViolationError``),
so no agent tool can add a new file read without passing through the
validator. Every file-touching module starts with the applicable
assertion.

**Alternatives.** None seriously considered — the zone model is
explicitly stated in HIPAA + ICMR + the RePORT India Common Protocol.
The only question was whether to enforce it as comments or as code;
code won.

**Consequences.** Refactors that move file I/O from one module to
another must also move (or add) the zone assertion. Tests catch missing
assertions via the "zone" test category in ``tests/test_secure_env.py``.

ADR-008 — Agent boundary PHI + k-anon gate as defence-in-depth
--------------------------------------------------------------

**What.** Every LLM tool return string runs through
:func:`scripts.security.phi_gate.phi_gate_check`. Row-level returns
additionally run through :func:`scripts.security.kanon_gate.kanon_check`
with k=5.

**Why.** The offline scrub (Step 1.6) is the primary PHI defence; the
agent-boundary gate is the backstop. If a new narrative-field shape
slips past the catalog or a quasi-identifier equivalence class is
smaller than k=5 for a specific query, the gate catches the leak
before the model ever sees it.

**How.** :func:`scripts.ai_assistant.phi_safe.phi_safe_return` decorator;
:func:`scripts.ai_assistant.phi_safe.guard_rows_with_kanon` for
row-level surfacing.

**Alternatives.** "Trust the offline scrub, skip the runtime gate".
Rejected — single-layer defences against PHI leakage have a bad track
record across the industry. The k-anon gate is particularly important
for the Indo-VAP cohort size (tribal + district + outcome combinations
can trivially produce k<5 equivalence classes).

**Consequences.** Every tool author must decorate with
``@phi_safe_return``. Missing decorations are caught by the
``test_agent_tools_zone_guard`` coverage tests (see
:doc:`testing`).

ADR-009 — Counts-only audit reports (never raw values)
------------------------------------------------------

**What.** Every report under ``output/{STUDY}/audit/`` records counts
of actions per field per file but never the underlying values. The
lineage manifest records hashes, not contents.

**Why.** An auditor must be able to verify the scrub ran correctly
without access to the raw data. Putting raw values in the audit would
turn every audit report into a PHI leak surface of its own.

**How.** :func:`scripts.security.phi_scrub._emit_audit` emits a payload
with ``scrubbed: [{scope, field, file, count}, ...]``. The lineage
manifest records ``{path, sha256, size_bytes, mtime_utc}`` entries.

**Alternatives.** A verbose audit with before/after value pairs was
considered for debugging; rejected — such an audit is a full PHI copy.
If debugging requires value inspection, the operator reads the
(short-lived, mode-0700) AMBER staging files directly, never the
audit.

**Consequences.** Audit reports are thin on evidence by design. The
counts suffice for IRB acceptance; deeper debugging requires live
staging inspection.

ADR-010 — Subprocess + rlimits sandbox for ``run_python_analysis``
------------------------------------------------------------------

**What.** LLM-generated Python from the ``run_python_analysis`` tool
runs in a fresh OS subprocess with ``RLIMIT_AS`` / ``RLIMIT_NPROC``
/ ``RLIMIT_CPU`` clamps + a sanitised env + read-only access to the
trio bundle. The original AST guards remain as defense-in-depth
inside the child.

**Why.** AST guards alone don't stop CPython gadget escapes
(hand-crafted ``__class__.__bases__[0]`` traversals, ``co_consts``
poisoning, etc.). An OS-level isolation boundary defangs every
in-process escape: even a perfectly-jailbroken interpreter inside the
subprocess cannot exceed the rlimits, cannot read the parent's
KeyStore, cannot write outside ``trio_bundle/``, cannot fork beyond
``RLIMIT_NPROC``.

**How.** Implemented under
:mod:`scripts.ai_assistant.sandbox.replicate` (public API),
:mod:`scripts.ai_assistant.sandbox.runner` (child entry point), and
:mod:`scripts.ai_assistant.sandbox.limits` (rlimits helpers). The
generated ``.py`` file is persisted to
``output/{STUDY}/agent/analysis/{ts}.py`` so the operator can copy
+ reproduce externally — no hidden code path.

**Alternatives.**

* **AST guards only**. Rejected — known
  CPython escape gadgets defeat AST-level filtering.
* **``nsjail`` / ``firejail``** profile. Considered as a future
  high-assurance option for cloud deployments; not shipped because
  the deployment target is the operator's laptop and ``nsjail``
  needs root + Linux-specific kernel features.
* **WebAssembly** (Pyodide). Considered; rejected because pandas /
  numpy / scipy / statsmodels don't run there cleanly, and the
  capability surface ``run_python_analysis`` needs is exactly those
  libraries.

**Consequences.** Subprocess startup is ~75 % of per-call latency
on macOS — acceptable for the analytical-question-per-second
workload but would need parquet + ``mmap`` if call rate increased
materially. Tracked as future work in :doc:`sandbox`.

ADR-011 — KeyStore (in-memory API-key registry)
-----------------------------------------------

**What.** API keys never persist in the parent process's
``os.environ`` for the lifetime of the app. The Streamlit wizard
routes the key into an in-memory ``KeyStore`` registry; the
corresponding ``*_API_KEY`` env variable is scrubbed from
``os.environ``. Keys are re-injected only into the short-lived
pipeline subprocess via ``KeyStore.env_for_subprocess``.

**Why.** ``os.environ`` is a process-wide global. A single
``logger.info(f"env={dict(os.environ)}")`` debug-print, an exception
traceback rendered with ``locals()``, or a third-party library
helpfully echoing config — any of these can leak ``ANTHROPIC_API_KEY``
into the log file and from there into the audit envelope or the
operator's terminal scrollback. Removing the keys from ``os.environ``
once they're loaded into the in-memory registry shrinks the leak
surface materially.

**How.** Implemented as
:mod:`scripts.ai_assistant.keystore`. Every LLM client constructor
(``ChatAnthropic``, ``ChatOpenAI``, ``ChatGoogleGenerativeAI``,
``ChatNVIDIA``, ``ChatOllama``) takes an explicit ``api_key=`` kwarg
sourced from the KeyStore — no environment lookup at construction
time. Tested by ``tests/test_keystore.py``,
``tests/test_log_hygiene_keys.py``,
``tests/test_no_keys_in_parent_environ.py``.

**Alternatives.**

* **OS keyring** (``keyring`` package). Considered for persistence
  across sessions; rejected because the operator already has the
  ``.env`` file as their persistence layer, and adding keyring would
  introduce platform-specific behaviour (macOS Keychain vs Linux
  libsecret vs Windows Credential Vault).
* **Encrypted on-disk vault**. Rejected — adds a master-key bootstrap
  problem on top of the existing PHI-key bootstrap problem.

**Consequences.** Operators using the CLI (``python main.py
--pipeline`` directly without the wizard) still rely on the env-var
path. The CLI ``main.py`` reads ``LLM_PROVIDER`` / ``ANTHROPIC_API_KEY``
from env; this is intentional for back-compat with existing
shell-script invocations. The KeyStore posture only applies to the
in-app (Streamlit/CLI-REPL) lifetimes.

ADR-012 — Two-way PDF orchestrator (pdfplumber + redacted-text LLM merge)
-------------------------------------------------------------------------

.. note::

   **Status: Superseded / Removed in Phase 5b.** The PDF extraction
   pipeline (``scripts/extraction/pdf_pipeline.py``,
   ``scripts/extraction/extract_pdf_data.py``, and
   ``scripts/utils/llm_capabilities.py``) was retired in Phase 5b after
   the trio bundle / catalog cutover made the source-truth catalog the
   sole metadata layer. The ADR is preserved as historical record; the
   modules, snapshot baseline, and agent ``search_pdf_context`` tool no
   longer exist.

**What.** PDF extraction has two co-existing paths. The default path
(``scripts/extraction/pdf_pipeline.py``, the wizard's "Load Study"
selection) extracts text locally with ``pdfplumber``, redacts the
text via ``phi_patterns.BLOCKING_PATTERNS`` *before* any LLM call,
sends only the redacted text to a capable LLM, re-scrubs the
response, and merges with the code candidate. Per-PDF fallback to
the reviewed snapshot baseline at ``data/snapshots/{STUDY}/pdfs/``
when the LLM tier is unavailable. **No raw PDF bytes leave the host
on this path.**

**Why.** ADR-006 refused external-API PDF extraction by default
because raw PDF bytes carry PHI risk. The two-part attestation gate
in the legacy path is a workable but operator-friction-heavy
posture. The orchestrator path replaces "ship raw bytes after
attestation" with "redact text first, then ship". The redaction
catalog is the same one the agent-output PHI gate uses, so the
audit story is internally consistent.

**How.** Implemented under
:mod:`scripts.extraction.pdf_pipeline`. Capability gate via
:func:`scripts.utils.llm_capabilities.is_capable_model` (Claude Opus
4.6+, Sonnet 4.6+, GPT-5+, Gemini 2.5 Pro, Llama 3.3 405B; Ollama
excluded by default). Provider gate via
:data:`scripts.extraction.pdf_pipeline.ORCHESTRATOR_SUPPORTED_PROVIDERS`
(anthropic + google only — where ``_extract_via_llm`` has client
wiring). Idempotent cache keyed on
``SHA-256(pdf_bytes || provider || model || phi_scrub.yaml hash)``.

**Alternatives.**

* **Local-only PDF extraction** (pdfplumber + local-Ollama
  multimodal). Considered; rejected as primary because Ollama
  models don't reliably produce the JSON-schema response on a
  30-page CRF. The snapshot fallback covers this case.
* **Keep ADR-006's attestation gate as the only path.** Rejected
  because operator friction effectively meant the PDF leg was
  skipped for most runs; the orchestrator unblocks the leg without
  weakening the egress posture.

**Consequences.** ADR-006 is now a *fallback* path, not the primary
path. The attestation gate remains in the legacy
``extract_pdf_data._resolve_pdf_provider``. The orchestrator uses the
``data/snapshots/{STUDY}/`` baseline tier described in ADR-013.

ADR-013 — Single reviewed snapshot baseline
-------------------------------------------

.. note::

   **Status: Superseded / Removed in Phase 5d.** The reviewed snapshot
   baseline subsystem (``scripts/utils/snapshots.py``,
   ``scripts/utils/restore_drill.py``, and the wizard's "Use Existing
   Study" affordance) was retired in commit ``0ae138e`` after the
   source-of-truth-based extraction in Phase 5b made the published
   ``output/{STUDY}/llm_source/`` tree directly reviewable. The
   ``data/snapshots/{STUDY}/`` path constant is preserved only as a
   security-zone deny marker; no user-facing flow restores from it.
   The ADR is kept as historical record.

**What.** One human-reviewed copy of the trio bundle lived at
``data/snapshots/{STUDY}/``. It mirrored
``output/{STUDY}/trio_bundle/`` and served as the deterministic
fallback for the PDF orchestrator (ADR-012) and the wizard's
**Use Existing Study** button.

**Why.** At the time, the PDF orchestrator needed a reviewable
baseline to fall back on when the LLM tier failed, and the wizard
needed a clean recovery path from incomplete PDF extraction.

**Consequences of removal.** With the Phase 5b SoT extraction
producing a directly-reviewable ``llm_source/`` tree, the
baseline-mirror layer became redundant and was retired.

ADR-014 — Parallel extraction phase (3-worker ThreadPoolExecutor)
-----------------------------------------------------------------

**What.** ``main.py``'s extraction phase runs Dictionary / Datasets /
PDFs in parallel on a 3-worker ``concurrent.futures.ThreadPoolExecutor``.
The cleanup chain (PHI scrub / dataset cleanup / cleanup propagation),
publish, and ``variables.json`` build run sequentially after the
join.

**Why.** The three legs are fully decoupled — different RED inputs,
different AMBER staging subdirs, no shared mutable state. The PDF
leg's HTTP latency (orchestrator LLM calls) is amortised against
the dataset leg's Excel-parsing CPU. Cleanup chain stays sequential
because it has hard data dependencies (PHI scrub needs all dataset
records; propagation needs the cleanup audit; publish needs
propagation; ``variables.json`` needs publish to have landed).

**How.** Each leg is wrapped in a
helper (``_run_dict_leg``, ``_run_dataset_leg``, ``_run_pdf_leg``)
that returns a result dict; futures are gathered via
``as_completed`` so a fast leg can short-circuit while a slow one
runs.

**Alternatives.**

* **``multiprocessing.Pool``** for true parallelism. Rejected — the
  PDF leg's HTTP calls release the GIL, so threads are sufficient;
  ``multiprocessing`` would multiply the staging-tree write
  contention.
* **``asyncio``**. Rejected — the existing extraction code is sync
  pandas + openpyxl; converting to async would be a much bigger
  change.

**Consequences.**
The staging root is protected by a per-study process lock before
AMBER is purged, so two operator-triggered runs cannot race over the
same ``tmp/{STUDY}/`` workspace. ``VerboseLogger`` uses thread-local
indentation, keeping ``--verbose`` tree output readable while the
three extraction legs overlap.

ADR-015 — l-diversity (l=2) on row-returning tools
---------------------------------------------------

**What.** Every row-returning agent tool runs the result through
:func:`scripts.security.kanon_gate.guard_rows_with_kanon_and_ldiv`.
The gate enforces both k-anonymity (k=5; ADR-008 / Pillar 2.4) AND
l-diversity (l=2 distinct sensitive-attribute values per
equivalence class).

**Why.** k-anonymity alone is insufficient when every row in a
small equivalence class shares the same sensitive attribute (the
"homogeneity attack" of Machanavajjhala et al. 2007). If five
rows match on all quasi-identifiers AND all five share the
diagnosis ``HIV+``, the k=5 check passes but the cohort is just as
re-identifying as a k=1 disclosure. l-diversity guards against this
by also requiring at least ``l`` distinct values of the sensitive
attribute.

**How.** Default sensitive attributes
are configured in ``_DEFAULT_SENSITIVE_ATTRIBUTES`` of
:mod:`scripts.security.kanon_gate`. Tested by
``tests/security/test_kanon_l_diversity.py``.

**Alternatives.**

* **t-closeness.** Considered; deferred. t-closeness checks the
  *distribution* of the sensitive attribute against the population
  baseline, which needs population-level priors that aren't always
  available. l-diversity gives the bulk of the protection without
  that requirement; t-closeness can be added later as a third
  gate.
* **Differential privacy**. Out of scope for the row-returning-tool
  use case; would change the contract from "exact rows when allowed"
  to "noisy aggregate".

**Consequences.** Some legitimate cohort queries return aggregate
substitutes (``"≥5 subjects, all sharing diagnosis X"``) instead
of row-level data. The agent's prompt explains this to the user
when the gate fires.
