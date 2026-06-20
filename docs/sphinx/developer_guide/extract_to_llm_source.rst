extract_to_llm_source Skill
===========================

This page is for operators, maintainers, and AI coding assistants that
need the canonical raw workbook to PHI-clean ``llm_source/`` entry
point. After reading it, you should know what the skill does, which
inputs it trusts, which artifacts prove a run, and which hardening work
is still open.

Implementation:
``scripts/skills/extract_to_llm_source.py``.

Agent-facing skill:
``skills/dataset-to-llm-source/SKILL.md``. The skill is a thin
workflow wrapper around the CLI; the CLI remains the source of behavior.

Scope and Boundary
------------------

The skill drives one study from raw workbook inputs to the published
assistant source:

.. code-block:: text

   data/raw/{STUDY}/datasets/*.xlsx
        -> tmp/{STUDY}/datasets/*.jsonl
        -> scripts.security.phi_scrub.run_scrub
        -> output/{STUDY}/llm_source/dataset_schema/files/*.jsonl

The LLM must not see raw dataset values, staged values, audit values, or
held-form details. Before publish, the approval step may use only:

* dataset headers read from row 1,
* study privacy configuration,
* jurisdiction rule metadata,
* generated approval artifacts that contain headers/actions only.

Real row values are opened only inside the trusted extraction and scrub
subprocess. After deterministic checks pass, the assistant may read only
``output/{STUDY}/llm_source/`` and ``output/{STUDY}/agent/`` through
the file-access validator.

Required Inputs
---------------

.. list-table::
   :header-rows: 1

   * - Input
     - Location
     - Purpose
   * - PHI HMAC key
     - Operator-managed sidecar outside the repo.
     - Loaded only by ``scripts.security.phi_scrub`` while rewriting
       staged values. Agent workflows and wrapper preflights must not
       read, print, hash, stat, permission-check, or existence-check
       key material.
   * - Forms manifest
     - ``data/raw/{STUDY}/_forms_manifest.yaml``
     - Declares required, optional, and rejected dataset files.
   * - Study privacy config
     - ``data/raw/{STUDY}/_study_privacy.yaml``
     - Declares jurisdictions, rule-refresh mode, conflict policy,
       approval retry policy, and parallelism.
   * - Raw datasets
     - ``data/raw/{STUDY}/datasets/``
     - Source workbook files.
   * - PHI scrub catalog
     - ``scripts/security/phi_scrub.yaml``
     - Canonical real-data scrub behavior.

Commands
--------

Run:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
     --study Indo-VAP

Verify the latest run:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
     --study Indo-VAP

Verify a specific run:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
     --study Indo-VAP --run RUN_ID

Print the scope banner and exit-code contract:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py status

Use ``--max-workers N`` on ``run`` to bound parallel header-review
workers. Use ``--form DATASET`` to run one manifest-declared dataset at
a time; the value may be the full filename, such as ``6_HIV.xlsx``, or
the dataset stem, such as ``6_HIV``. Repeat ``--form`` to run a small
explicit set.

One-dataset pilot:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
     --study Indo-VAP --form 6_HIV

Run Flow
--------

1. Resolve a run id and create ``output/{STUDY}/runs/{run_id}/``.
2. Fail closed if ``REPORTALIN_ALLOW_DISABLED_SCRUB`` is set.
3. Check for in-progress scrub recovery tokens.
4. Acquire the study pipeline lock.
5. Validate ``_forms_manifest.yaml`` against the dataset directory.
6. Load ``_study_privacy.yaml``.
7. Refresh jurisdiction source metadata from official URLs when allowed,
   otherwise use the pinned rule pack.
8. Read row-1 headers for each reviewed form.
9. Classify headers with strictest-wins rule merging and write
    ``phi_handling_approval.json``.
10. Pass approved forms, including any ``--form`` subset, to the main pipeline through
    ``REPORTAL_ALLOWED_DATASET_FORMS``.
11. Run the host publish engine (``scripts/pipeline/host_pipeline.py``) in-lock.
    The PHI scrub step loads the key internally; the wrapper does not inspect
    key material. (``main.py`` has no pipeline flag — it is the AI-assistant
    launcher only; publish runs inside ``make study``.)
12. Assert required per-dataset PHI ledger hashes and empty quarantine.
13. Destroy ``tmp/{STUDY}/`` after successful publish and write
    ``destruction_attestation.json``.
14. Write terminal ``status.json``.

Approval Artifacts
------------------

The approval report is:

.. code-block:: text

   output/{STUDY}/runs/{run_id}/phi_handling_approval.json

It contains headers, selected actions, rule-bundle metadata, source
hashes when fetched, approved forms, and held forms. It must not contain
raw row values or synthetic row values.

Current behavior to keep exact:

* official source URLs are allowlisted before network access;
* fetched official pages are retained only as hashes in the run audit;
* rules applied to headers come from the pinned local rule pack;
* generated transform source can be statically validated, but generated
  transform code is not the canonical real-data scrub path;
* canonical real-data scrub behavior remains
  ``scripts/security/phi_scrub.py`` plus ``phi_scrub.yaml``.

Exit Codes
----------

.. list-table::
   :header-rows: 1

   * - Code
     - Constant
     - Meaning
   * - 0
     - ``EXIT_OK``
     - Full success.
   * - 1
     - generic
     - Unexpected Python exception.
   * - 2
     - ``EXIT_MANIFEST_MISMATCH``
     - Manifest and raw dataset directory disagree.
   * - 3
     - ``EXIT_LEDGER_HASH_NULL``
     - Ledger hash or no-LLM sentinel assertion failed.
   * - 4
     - ``EXIT_QUARANTINE_NON_EMPTY``
     - Quarantine contains files.
   * - 5
     - ``EXIT_VERIFIER_FAIL``
     - Verifier assertion failed.
   * - 6
     - ``EXIT_NEEDS_ADVICE``
     - Operator review required.
   * - 7
     - ``EXIT_DESTRUCTION_INCOMPLETE``
     - Staging was not fully removed or attested.
   * - 8
     - ``EXIT_PARTIAL_REVIEW``
     - Approved forms published, held forms need review.
   * - 9
     - ``EXIT_DECISION_MISMATCH``
     - phi_review decision ≠ ledger-applied scrub action (protection-lattice
       under-protection detected).
   * - 10
     - ``EXIT_AUDIT_COVERAGE_INCOMPLETE``
     - Published column has no PHI ledger entry and no non-keep configured
       scrub rule.

Verifier Assertions
-------------------

``verify`` writes
``output/{STUDY}/runs/{run_id}/verifier_report.json`` on pass or fail.
It checks 16 assertions; execution order is 1→12, then 14, 15, 16, then 13
(13 always runs last as the terminal status update):

1. manifest exists and parses;
2. manifest reconciles with the dataset directory;
3. staging is absent after successful publish;
4. destruction attestation exists and has required fields;
5. per-dataset PHI ledger hashes are present and match the scrub config;
6. the audit envelope has the no-LLM sentinel;
7. quarantine is absent or empty;
8. ``llm_source/`` has no blocking PHI pattern findings;
9. ``llm_source/`` has no runtime key material;
10. required or approved dataset JSONL files exist;
11. the pipeline lock is absent;
12. decided action matches applied — cross-checks each approved form's
    applied protection (ledger events + keep_decisions) against
    ``phi_review``'s decided action via the protection lattice; fails
    only under-protection (applied rank < decided rank); exits 9;
13. ledger covers all columns (assertion 14) — every published dataset
    column is accounted for by a PHI ledger entry or a non-keep
    configured scrub rule; exits 10;
14. SoT joined view present (assertion 15) — when SoT pairs exist, the
    joined query view is published and is the sole LLM-facing SoT file;
15. ledger entry fields complete (assertion 16) — every PHI ledger event
    carries a ``method``, ``rule.jurisdictions``, and a why
    (``rule.taxonomy`` or ``rationale``); exits 10;
16. ``status.json`` exists and is updated (assertion 13) with
    ``verifier_passed: true`` on full pass (always runs last).

Destruction Attestation
-----------------------

After successful publish, the skill writes:

.. code-block:: text

   output/{STUDY}/runs/{run_id}/destruction_attestation.json

The attestation records start and completion timestamps, the destroyed
staging path, relative removed paths, destroyed-file count, and the APFS
copy-on-write caveat. It is operational-untraceability evidence, not a
claim of forensic erasure.

Open Hardening Work
-------------------

These items must stay visible until fixed in code and tests:

* validate ``REPORTAL_RUN_ID`` before using it as a path component;
* include present optional forms in the header approval allowlist, or
  fail closed when no explicit allowlist is produced;
* hash-bind each raw workbook at header approval and recheck the hash
  immediately before real extraction;
* scan the full assembled ``llm_source`` publish candidate, not only
  staged dataset JSONL;
* report rule refresh as pinned-with-official-source-hashes unless
  parsed official rules are actually loaded.

Focused Tests
-------------

.. code-block:: bash

   uv run --all-groups python -m pytest \
     tests/security/test_phi_review.py \
     tests/skills/test_extract_to_llm_source_cli.py \
     tests/skills/test_extract_to_llm_source_verify.py \
     tests/test_dataset_pipeline.py \
     tests/test_file_access.py
