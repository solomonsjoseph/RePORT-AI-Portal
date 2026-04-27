Glossary
========

**What.** Short, authoritative definitions for every term of art this
project uses.

**Why.** Researcher, data-manager, and IRB-reviewer audiences arrive with
different vocabularies — a clinician reads "trio bundle" differently from
a security engineer reading the same words. One page fixes the meanings so
the rest of the docs can reference them without re-defining.

**How.** Alphabetical. Each entry uses the What / Why / How micro-pattern:
what the term means, why it matters for this project, how it maps to a
code module or audit artifact.

.. glossary::

   AMBER zone
     **What.** The transient staging workspace at ``tmp/{STUDY_NAME}/``
     (or ``/dev/shm/{STUDY}/`` when ``REPORTALIN_TMPFS_STAGING=1`` on Linux).
     **Why.** All extraction, scrub, cleanup, and propagation happen here
     — nothing permanent is produced until the AMBER → GREEN atomic
     publish step. The LLM agent never reads AMBER.
     **How.** Hardened by :func:`scripts.utils.secure_staging.prepare_staging`
     (mode 0700, umask 0077); zero-filled + unlinked by
     :func:`scripts.utils.secure_staging.secure_remove_tree` on success.

   audit report
     **What.** A JSON file under ``output/{STUDY}/audit/`` that records
     counts of scrub / cleanup actions per field per file, but NEVER the
     underlying values.
     **Why.** Regulators verify the de-identification story from the
     audit alone without ever reading the scrubbed data. Counts-only is a
     hard invariant: any future change that adds raw values to an audit
     is a compliance regression.
     **How.** Four reports today — ``phi_scrub_report.json``,
     ``dataset_cleanup_report.json``, ``dictionary_cleanup_report.json``,
     ``pdfs_cleanup_report.json`` — plus the cross-cutting
     ``lineage_manifest.json``.

   cap (action)
     **What.** A PHI-scrub action that replaces a numeric value strictly
     greater than a threshold with a fixed label. Default: age > 89 →
     ``"90+"``.
     **Why.** HIPAA §164.514(b)(2)(i)(C) requires ages over 89 aggregated
     to a single category; ICMR §11.7 adds k-anonymity concerns on very
     rare age tails.
     **How.** :func:`scripts.security.phi_scrub.cap_numeric`;
     rules declared in ``phi_scrub.yaml`` under ``cap_fields``.

   compliance posture
     **What.** The ``phi_scrub.yaml`` flag choosing the disposition of
     the birthdate field. Two values: ``safe_harbor`` (default) drops DOB
     entirely; ``limited_dataset`` shifts DOB with the same per-subject
     SANT offset as other dates, preserving age-at-event.
     **Why.** HIPAA Safe Harbor forbids full birthdate retention; the
     Limited Dataset pathway relaxes that when an IRB-approved DUA
     justifies age-at-event retention.
     **How.** Enforced by :mod:`scripts.security.phi_scrub`; Limited
     Dataset mode additionally requires
     ``authorities/phi_limited_dataset.md`` to exist before the scrub
     runs.

   drop (action)
     **What.** A PHI-scrub action that removes a field entirely from every
     row.
     **Why.** The most aggressive response for direct identifiers that
     have no analytic value (names, Aadhaar, PAN, voter ID, exact
     address, narrative free-text).
     **How.** Rules declared in ``phi_scrub.yaml`` under ``drop_fields``
     (~93 rules for Indo-VAP).

   generalize (action)
     **What.** A PHI-scrub action that maps a field's value to a broader
     category using a named dict.
     **Why.** Preserves analytic signal (marital status still stratifies
     outcomes) while collapsing identifying granularity (exact free-text
     like "separated/divorced with three children" → ``Other``).
     **How.** Rules in ``phi_scrub.yaml`` under ``generalize_fields``;
     value-to-value maps under ``generalization_maps``.

   GREEN zone
     **What.** The PHI-free published artifact tree at
     ``output/{STUDY_NAME}/trio_bundle/``.
     **Why.** One of the two zones the LLM agent reads (the other is
     ``output/{STUDY_NAME}/agent/``, which stores the agent's own analysis
     output, conversations, and snapshots). Every @tool in
     :mod:`scripts.ai_assistant.agent_tools` resolves every file path through
     :func:`scripts.ai_assistant.file_access.validate_agent_read` (the
     unified agent-zone chokepoint) before any file I/O.
     :func:`scripts.security.secure_env.assert_trio_bundle_zone` remains as
     a directory-level early-reject at the pipeline-side boundary.
     **How.** Produced by the Step-2 atomic publish — AMBER staging legs
     rename into GREEN once the scrub + cleanup + propagation steps
     succeed.

   GREEN-PROTECT
     **What.** The defence-in-depth layer between GREEN and the LLM. Every
     agent-tool return runs through :func:`scripts.security.phi_gate.phi_gate_check`
     (regex + allowlist) and, for row-level returns, through
     :func:`scripts.security.kanon_gate.kanon_check` (k-anonymity ≥ 5).
     **Why.** If the offline scrub missed a PHI token, the query-time
     gate catches it before the string reaches the model.
     **How.** :func:`scripts.ai_assistant.phi_safe.phi_safe_return`
     decorator wraps tool returns; redacted responses replace PHI with a
     standard suppression message.

   honest broker
     **What.** A neutral intermediary that removes identifiers from
     clinical data before releasing it to researchers.
     **Why.** Canonical regulatory pattern (HIPAA / OHRP) — researchers
     get the data they need without ever touching the identified record.
     **How.** RePORT AI Portal implements the honest broker as code, not
     as a human role: raw data in RED, scrubbed data in GREEN, PHI
     transformations happen in AMBER in between.

   HMAC key
     **What.** A 32-byte secret that keys the HMAC-SHA256 pseudonymization
     + date-offset algorithms.
     **Why.** Two properties that plain hashing cannot provide: (a) the
     same subject-id always maps to the same pseudonym for the lifetime
     of the key (longitudinal linkage preserved); (b) without the key,
     the pseudonym is non-reversible (one-way).
     **How.** Generated by ``python -m scripts.security.phi_scrub bootstrap-key``
     and stored at ``~/.config/report_ai_portal/phi_key`` (mode 0600).
     Never inside the repo tree.

   jitter (action)
     **What.** A PHI-scrub action that shifts every date for a subject by
     the same deterministic offset in ``[-max_jitter_days, +max_jitter_days]``.
     **Why.** Preserves per-subject date intervals exactly (so survival
     / incidence / person-time analyses run unchanged) while obscuring
     the exact calendar date of each event.
     **How.** SANT method — offset derived from
     ``HMAC-SHA256(key, subject_id)[:4]`` mod the jitter envelope. See
     :func:`scripts.security.phi_scrub.shift_date`.

   k-anonymity
     **What.** A privacy guarantee: every equivalence class of
     quasi-identifiers (age band × sex × district × outcome) must contain
     at least *k* records.
     **Why.** A single row with a rare combination can re-identify a
     subject even after direct identifiers are removed.
     **How.** :func:`scripts.security.kanon_gate.kanon_check` runs at the
     agent-tool boundary with *k* = 5 by default; responses whose
     smallest equivalence class is smaller than *k* are blocked.

   keep (allowlist)
     **What.** A PHI-scrub action that explicitly *preserves* a field,
     short-circuiting every other rule.
     **Why.** Clinical lab / medication / time-of-day / categorical
     indicator columns must survive the scrub; the keep allowlist
     guarantees broader drop patterns cannot swallow them.
     **How.** Rules in ``phi_scrub.yaml`` under ``keep_fields`` (~80
     rules for Indo-VAP). Evaluated first in the priority dispatch.

   lineage manifest
     **What.** ``output/{STUDY}/audit/lineage_manifest.json`` — one JSON
     file per pipeline run pairing every raw input's SHA-256 with every
     published trio artifact's SHA-256, plus per-leg audit references
     and compliance posture.
     **Why.** The single evidence artifact an IRB reviewer consults to
     verify the raw → scrubbed → published chain is intact and
     reproducible.
     **How.** :func:`scripts.utils.lineage.emit_lineage_manifest`,
     called from ``main.py`` as Step 4 after publish.

   provenance
     **What.** The ``_provenance`` dict attached to every row of every
     extracted JSONL. Fields: ``source_file``, ``sheet_name``,
     ``row_index``, ``study_name``, ``extraction_utc``,
     ``pipeline_version``, ``extraction_engine``, ``raw_sha256``.
     **Why.** Every cell in the trio bundle can be traced back to its
     source file + the exact pipeline version that produced it.
     Regulatory requirement for reproducibility (STROBE / RECORD / FDA
     21 CFR Part 11).
     **How.** Emitted by
     :func:`scripts.extraction.dataset_pipeline._build_provenance` for
     every row.

   pseudonymize (id action)
     **What.** A PHI-scrub action that replaces a direct identifier value
     with ``SUBJ_<hmac-tag>``.
     **Why.** Linkage across forms survives (same subject → same tag)
     while the original identifier is non-recoverable without the HMAC
     key.
     **How.** :func:`scripts.security.phi_scrub.pseudo_id`; rules in
     ``phi_scrub.yaml`` under ``id_fields``.

   quasi-identifier
     **What.** A field that is not individually identifying but can
     re-identify a subject when combined with others — canonically age +
     sex + ZIP (or, in India, age + sex + district).
     **Why.** Dropping direct identifiers alone does not guarantee
     privacy: rare combinations of quasi-identifiers point to one
     person even in a large cohort.
     **How.** Defended by the k-anonymity gate at the agent boundary —
     small equivalence classes trigger suppression.

   RED zone
     **What.** The raw study tree under ``data/raw/{STUDY_NAME}/``.
     **Why.** Contains unscrubbed PHI. Must not be read by anything
     except the extraction leg.
     **How.** Guarded by
     :func:`scripts.security.secure_env.assert_not_raw`; any module
     attempting to read from ``data/raw/`` raises
     :class:`scripts.security.secure_env.ZoneViolationError`.

   SANT (Shift-And-Not-Truncate)
     **What.** A date-deidentification method: shift every date for a
     subject by a random offset drawn from a bounded envelope.
     **Why.** HIPAA Safe Harbor reduces dates to year only (information
     loss = ~364 days). SANT preserves intervals exactly while still
     obscuring the calendar anchor.
     **How.** Per-subject offset = ``HMAC-SHA256(key, subject_id)[:4]
     mod (2*max_days + 1) - max_days``. See
     :func:`scripts.security.phi_scrub.date_offset_days`.

   suppress_small_cell (action)
     **What.** A PHI-scrub action that clamps numeric values (household
     contact counts, prevalence numerators) to a small-cell threshold.
     **Why.** Rare high counts are re-identifying in their own right
     (exactly one subject has 12 contacts → that subject is identifiable).
     **How.** :func:`scripts.security.phi_scrub.suppress_small_cell`;
     rules in ``phi_scrub.yaml`` under ``suppress_small_cell_fields``.

   trio bundle
     **What.** The published, PHI-free artifact set under
     ``output/{STUDY_NAME}/trio_bundle/``. Name comes from the three
     companion directories — ``datasets/``, ``dictionary/``, ``pdfs/`` —
     that every researcher workflow needs together.
     **Why.** "Trio" signals that the three components are consistent
     with each other (same study, same publish timestamp, same audit
     trail).
     **How.** Produced by ``_publish_staging`` in ``main.py`` via atomic
     per-leg rename of the AMBER staging directories.
