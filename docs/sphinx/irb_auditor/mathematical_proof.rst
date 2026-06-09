Formal Containment Argument for the PHI Security Boundary
==========================================================

This appendix provides a formal, deterministic containment argument for
Protected Health Information (PHI) in the RePORT AI Portal: **within the
system model and assumptions stated below**, every modeled path by which
raw PHI could reach the LLM agent or an external cloud API is closed by a
non-probabilistic control, so the modeled leakage probability is zero:

.. math::

   P(\text{leak} \mid \text{model assumptions}) = 0

This is a *containment argument over the stated model*, not an
unconditional guarantee about all conceivable failure modes; the model's
assumptions and residual scope are listed explicitly in
:ref:`proof-assumptions` so an auditor can verify each one independently
against the conformance table in :doc:`conformance`.

System Model and Definitions
----------------------------

Let the system state be represented by a tuple of discrete filesystem
directories and memory spaces:

* :math:`D_{\text{raw}}`: The directory containing raw clinical source datasets (e.g., ``data/raw/Indo-VAP/``), containing PHI.
* :math:`S_{\text{stage}}`: The staging workspace directory (``tmp/Indo-VAP/datasets/``), used ephemerally during ingestion.
* :math:`D_{\text{pub}}`: The clinical dataset read directory exposed to the agent (either the live ``output/Indo-VAP/llm_source/`` or, upon selecting a verified snapshot :math:`S_k`, the snapshot's scrubbed subtree :math:`S_k^{\text{llm}} = \text{output/Indo-VAP/snapshots/}\{snapshot\_id\}\text{/llm\_source/}`).
* :math:`\mathcal{S}`: The set of all study snapshots :math:`S_k` under ``snapshots/``. Each snapshot contains a scrubbed subtree :math:`S_k^{\text{llm}} = S_k/\text{llm\_source}/` and a denied metadata region :math:`S_k^{\text{meta}} = S_k \setminus S_k^{\text{llm}}` containing approval files and verifier reports.
* :math:`W_{\text{agent}}`: The agent execution workspace directory (``output/Indo-VAP/agent/``).
* :math:`K_{\text{HMAC}}`: The 32-byte cryptographic key with permissions ``0600`` stored at the configuration path.
* :math:`U`: The set of all user prompts submitted to the assistant.
* :math:`T`: The set of all text blocks returned by tools in ``ALL_TOOLS``.
* :math:`R(A)`: The set of files readable by the running LLM Agent process :math:`A`.

Formal Invariants
-----------------

Invariant 1: Ahead-of-Time (AOT) Deterministic Ingestion
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The scrubbing engine :mod:`scripts.security.phi_scrub` processes every
row :math:`r_i` in each dataset file :math:`F \in S_{\text{stage}}`
before the agent process can exist. Every published field value is the
output of exactly one configured scrub action (priority-ordered:
force-drop, keep, birthdate, drop, cap, generalize, band,
suppress-small-cell, date jitter, ID pseudonymization), and a row that
cannot be fully processed is **never promoted**:

* A row whose value cannot be parsed or mapped under its matched rule
  (an unresolvable date, an unmapped band/generalize category) is
  **quarantined**: partially scrubbed, written to the staging quarantine
  zone, and excluded from :math:`D_{\text{pub}}`. In strict mode the
  first such row aborts the entire study run instead.
* A row with no resolvable subject identifier (an *orphan*) cannot be
  deterministically date-jittered and is likewise quarantined, never
  promoted.
* A form whose held fraction exceeds the configured review cap is still
  published **minus its quarantined rows** and flagged ``elevated`` for
  operator review — every row that *was* published passed its row-level
  scrub individually. An elevated form is *incomplete*, never *corrupt*.

Formally, for every quarantined row:

.. math::

   r_i \in D_{\text{quarantine}} \subset S_{\text{stage}} \quad \text{and} \quad r_i \notin D_{\text{pub}}

Columns the regulatory review decided to *keep* are published verbatim by
design (they carry no PHI under the active rule bundle); their coverage
is enforced not by value parsing but by the verifier chain below
(decided-vs-applied, ledger coverage) plus the residual leak gate.
Because the scrubbing loop finishes before the agent process starts, the
raw datasets :math:`D_{\text{raw}}` and staging datasets
:math:`S_{\text{stage}}` are temporally and spatially isolated from
:math:`D_{\text{pub}}`.

The Decided-vs-Applied Protection Lattice
*****************************************

To prevent human configuration drift or accidental bypasses, the post-publish verifier enforces a **Protection Lattice** (Assertion 12) mapping actions to ordered protection ranks:

.. math::

   \text{Rank}(\text{action}) = \begin{cases}
       0 & \text{if } \text{action} = \text{keep} \\
       1 & \text{if } \text{action} \in \{ \text{generalize}, \text{band}, \text{cap}, \text{suppress} \} \\
       2 & \text{if } \text{action} \in \{ \text{jitter\_date}, \text{pseudonymize} \} \\
       3 & \text{if } \text{action} \in \{ \text{drop}, \text{birthdate\_drop} \}
   \end{cases}

.. note::

   The lattice uses the audit-ledger action names. Two of them appear
   under different labels elsewhere in this documentation:
   ``jitter_date`` is the scrub catalog's *date* rule (SANT per-subject
   date offset, ``date_fields`` in ``phi_scrub.yaml``) and
   ``pseudonymize`` is the *id* rule (HMAC subject-ID pseudonymization,
   ``id_fields``). They are the same operations.

For every published column, the verifier checks the applied action against the regulatory decided action from the header approval report, asserting:

.. math::

   \text{Rank}(\text{applied}) \ge \text{Rank}(\text{decided})

Any under-protection where :math:`\text{Rank}(\text{applied}) < \text{Rank}(\text{decided})` (e.g., deciding to drop a column but only generalizing it) triggers a fail-closed rejection. Over-protection where the applied rule is stronger than decided is safely permitted. A companion assertion (Assertion 14, *ledger coverage*) independently requires every published column to be accounted for — by a ledger event, a deliberate keep decision, or a configured non-keep rule — so no column's handling can be silently absent from the audit trail.

The Residual Leak Gate
**********************

Independent of the scrub itself, the shared residual scanner
:func:`scripts.security.llm_source_gate.scan_tree_for_phi` re-scans the
**published tree** for structural PHI patterns at three points: (1) at
publish time, before staging is promoted to ``llm_source/``; (2) at
snapshot activation, before the agent read zone is repointed at a
snapshot subtree; and (3) at every agent session start, before the agent
is constructed. A positive finding at any of the three points is
fail-closed: promotion, activation, or agent start is refused. This gate
operates on the *output* of the scrub, so a scrub defect in pattern-class
PHI is caught even though the scrub already ran.

.. admonition:: Lemma 1: Exclusion of Raw PHI
   :class: note

   Under Invariant 1 (row-level fail-closed scrub, quarantine
   never-promote) together with the verifier chain (Assertions 12 and
   14) and the three-point residual leak gate, the published directory
   contains no raw, un-jittered, or un-pseudonymized clinical PHI
   variables:

   .. math::

      D_{\text{pub}} \cap (D_{\text{raw}} \setminus \text{Scrubbed}) = \emptyset

Invariant 2: Logical Fencing of the Agent Read Zone
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Let :math:`\gamma_{\text{zone}}(p)` be the validation guard function defined in :mod:`scripts.audit.zone_guards`. The guard resolves the absolute realpath of any file request :math:`p` and enforces:

.. math::

   \gamma_{\text{zone}}(p) = \begin{cases}
       \text{Allow} & \text{if } \text{realpath}(p) \subseteq D_{\text{pub}} \cup W_{\text{agent}} \\
       \text{Deny} & \text{otherwise}
   \end{cases}

Any violation raises an :class:`scripts.audit.zone_guards.AuditZoneViolation` or :class:`scripts.audit.zone_guards.SnapshotZoneViolation` and halts execution.

Snapshot Isolation and Root Fencing
***********************************

When a verified study snapshot :math:`S_k` is selected via the Load Study UI, the process-global configuration is atomically repointed:

.. math::

   D_{\text{pub}} = S_k^{\text{llm}}

To guarantee that the agent cannot read raw/un-scrubbed clinical records or bypass verifications by reading parent directories, the guard :math:`\gamma_{\text{snapshot}}(p)` (implemented as :func:`scripts.audit.zone_guards.deny_if_snapshot_root`) denies any path :math:`p` resolving within the metadata region of any snapshot:

.. math::

   \gamma_{\text{snapshot}}(p) = \begin{cases}
       \text{Deny} & \text{if } \exists S_k \in \mathcal{S} \text{ such that } \text{realpath}(p) \subseteq S_k \text{ and } \text{realpath}(p) \not\subseteq S_k^{\text{llm}} \\
       \text{Allow} & \text{otherwise}
   \end{cases}

Since :math:`S_k^{\text{meta}}` contains sensitive run configuration and verification metadata (such as ``phi_handling_approval.json``, ``verifier_report.json``, and ``snapshot_manifest.json``), the combination of :math:`\gamma_{\text{zone}}(p)` and :math:`\gamma_{\text{snapshot}}(p)` guarantees that metadata remains strictly inaccessible to the agent process:

.. math::

   \forall S_k \in \mathcal{S}, \quad S_k^{\text{meta}} \cap R(A) = \emptyset

.. admonition:: Lemma 2: Fencing Boundary
   :class: note

   The files readable by the agent process are strictly bounded:

   .. math::

      R(A) \subseteq D_{\text{pub}} \cup W_{\text{agent}}

   Therefore, raw databases, administrative logs, and HMAC keys are inaccessible:

   .. math::

      D_{\text{raw}} \cap R(A) = \emptyset, \quad \text{Audit} \cap R(A) = \emptyset, \quad K_{\text{HMAC}} \cap R(A) = \emptyset

   Note that :math:`W_{\text{agent}}` contains only artifacts the agent
   itself derived from gated reads — its contents are downstream of
   :math:`D_{\text{pub}}`, so the bound on :math:`R(A)` reduces to the
   integrity of :math:`D_{\text{pub}}` (Lemma 1).

Invariant 3: Double-Ended Sanitization Gates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Let :math:`R_{\text{PII}}` be the set of deterministic regular expression patterns representing **structural identifiers** (e.g., Aadhaar numbers with Verhoeff checksum validation, PANs, phone numbers, emails). Let :math:`M(x)` be a boolean matching function:

.. math::

   M(x) = \begin{cases}
       1 & \text{if } \exists p \in R_{\text{PII}} \text{ that matches a substring in } x \\
       0 & \text{otherwise}
   \end{cases}

**Input Gate.** For any prompt :math:`u \in U`:

.. math::

   \text{If } M(u) = 1 \implies A(u) \text{ is not executed and a refusal message is displayed.}

**Output Gate.** For any tool return :math:`t_j \in T` generated by the execution of a tool function:

.. math::

   t_{\text{filtered}} = \text{guard\_text}(t_j) = \begin{cases}
       \text{Redaction\_Msg} & \text{if } M(t_j) = 1 \\
       t_j & \text{otherwise}
   \end{cases}

These regex gates are deterministic and non-probabilistic, but their
coverage is **structural identifiers only**. They are the *second* line
of defense: non-structural PHI (free-text names, narratives) is excluded
upstream because it never enters :math:`R(A)` at all (Invariants 1–2 —
free-text and direct-identifier columns are dropped or held at scrub
time, and quasi-identifier re-identification is bounded by the
k-anonymity / l-diversity guards and small-cell masking applied to
row-level tool output). The gates therefore close the residual
pattern-class channel, not the entire channel by themselves.

Theorem of Information Isolation
--------------------------------

.. admonition:: Theorem
   :class: important

   Let :math:`I_{\text{PHI}}` be the set of raw clinical PHI elements
   present in :math:`D_{\text{raw}}`. Under any sequence of researcher
   prompts :math:`U` and agent tool actions :math:`T`, and under the
   model assumptions below, no element of :math:`I_{\text{PHI}}` enters
   the LLM context or the network:

   .. math::

      \forall x \in I_{\text{PHI}}: \quad P(x \in \text{LLM\_Context} \mid \text{model assumptions}) = 0

.. admonition:: Proof
   :class: tip

   Suppose a leakage event occurs such that some element :math:`x \in I_{\text{PHI}}` is loaded into the active LLM context.
   For :math:`x` to be present in the LLM context, it must have entered through either:

   1. The user prompt: :math:`x \in u` for some :math:`u \in U`.
   2. A tool return: :math:`x \in t_{\text{filtered}}` for some :math:`t \in T`.
   3. Direct file reading by the agent: :math:`x \in p` for some :math:`p \in R(A)`.

   We analyze each case:

   * **Case 1:** If :math:`x \in u` and :math:`x` is a structural identifier, the prompt guard detects :math:`M(u) = 1`; the system refuses the request and the LLM is never invoked. (A researcher pasting non-structural PHI from outside the system is outside the model — the system cannot have *leaked* what it never held; see scope note below.) Thus :math:`x \notin \text{LLM\_Context}`. Contradiction.

   * **Case 2:** If :math:`x \in t_{\text{filtered}}`, the tool that produced :math:`t_j` can only have read from :math:`R(A)` (Lemma 2), whose contents are PHI-free (Lemma 1); and if :math:`x` were nonetheless present as a structural identifier, :math:`M(t_j) = 1` forces :math:`t_{\text{filtered}} = \text{Redaction\_Msg}`. Either way :math:`x \notin t_{\text{filtered}}`. Contradiction.

   * **Case 3:** If :math:`x \in p` for some file :math:`p \in R(A)`, then by Lemma 2, :math:`p \subseteq D_{\text{pub}} \cup W_{\text{agent}}`. By Lemma 1 (scrub + verifier chain + residual leak gate), :math:`D_{\text{pub}}` contains no raw PHI, and :math:`W_{\text{agent}}` is downstream of :math:`D_{\text{pub}}`. Thus :math:`x \notin p`. Contradiction.

   All modeled path scenarios lead to contradiction. Hence no element of
   :math:`I_{\text{PHI}}` can enter the LLM context through any modeled
   channel.

.. _proof-assumptions:

Model Assumptions and Residual Scope
------------------------------------

The argument above is exactly as strong as its premises. An auditor
should verify each of the following independently (the conformance table
in :doc:`conformance` maps them to evidence):

1. **Scrub-rule coverage** — the active rule bundle (HIPAA Safe Harbor +
   India DPDPA) correctly classifies every column; this is enforced
   operationally by the header review, the SoT cross-verification
   force-drop, and verifier Assertions 12 and 14, and is re-checked by
   the residual leak gate for pattern-class PHI.
2. **Filesystem enforcement** — OS file permissions and the
   ``realpath``-based zone guards behave as specified; the agent process
   is not run with elevated privileges.
3. **Side channels are separately controlled** — pre-scrub *log* output
   is redacted by the root-logger PHI filter
   (:func:`scripts.utils.log_hygiene.install_phi_redactor`), and
   generated *figure content* is gated before its path is surfaced
   (Plotly JSON is scanned as text). One documented residual gap
   remains: PHI baked into a **rasterized** matplotlib PNG cannot be
   text-scanned; this is mitigated upstream (the sandbox only reads
   PHI-free DataFrames) and is tracked as a known limitation, not
   covered by this argument.
4. **Out-of-band researcher input** — PHI a researcher obtains outside
   the system and pastes into a prompt was never held by the system;
   the input gate still refuses structural identifiers as
   defense-in-depth.
