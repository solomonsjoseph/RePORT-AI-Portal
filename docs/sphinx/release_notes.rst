Release Notes
=============

Release notes are written for people who need to know what changed
without reading commits. Keep entries short, current, and grouped by
impact.

Unreleased
----------

Added
~~~~~

* Added Sphinx runbooks for Source Truth builds and the
  ``extract_to_llm_source`` skill so user and collaborator docs stay in
  the Sphinx site instead of standalone Markdown files.
* Added production fail-closed controls for PHI log redaction, app/proxy rate
  limiting, CSP enforcement, direct virtualenv service execution, and stricter
  production study resolution.
* Added production-readiness controls for proxy authentication, healthcheck
  wiring, release tagging, restore-drill automation, and root security/license
  governance files.
* Added a production backlog page for deferred hardening items such as SBOMs,
  dependency-update automation, hosted-LLM budget limits, remote log sinks,
  conversation retention, and OCI packaging.
* Added Makefile targets for Sphinx release notes, docs linkcheck, and
  docs CI parity.
* Added this release-notes page to the Sphinx documentation.
* Added a contribution rule that every pull request should include a
  user-readable release note unless the change is purely internal and
  has no operator, reviewer, developer, or user impact.

Changed
~~~~~~~

* Clarified that the root README is the entry point and Sphinx is the
  durable documentation library. Root machine or GitHub metadata files
  should point readers back to Sphinx instead of carrying parallel
  runbooks.
* Updated Makefile cleanup targets to avoid whole-repo traversal and to
  preserve ``data/raw/`` and ``data/snapshots/``.
* Reworked the GitHub README as a minimal entry point that sends readers
  to Sphinx for setup, IRB/auditor evidence, and developer detail.
* Consolidated the Sphinx audience routing into the documentation
  landing page.
* Moved the IRB/auditor profile into Sphinx and removed the old
  standalone Markdown dossier.

Fixed
~~~~~

* Fixed local ``make chat`` startup when another Streamlit process already
  owns port 8501.
* Fixed production/proxy startup when the deployment uses an explicit or
  default study name without raw study input mounted at import time.
* Updated a sandbox regression test so its expected output no longer
  resembles a PHI phone-number pattern on Python 3.13.

extract_to_llm_source skill
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Added a CLI wrapper at ``scripts/skills/extract_to_llm_source.py`` that drives the
raw .xlsx → PHI-scrubbed ``llm_source/`` pipeline end-to-end for one study with
auditable gates and operationally-untraceable temp removal after publish.

Subcommands:

- ``run --study {STUDY}``: manifest pre-check → pipeline lock → scrubbed pipeline →
  ledger + quarantine assertions → secure staging destruction + attestation.
- ``verify --study {STUDY} [--run RUN_ID]``: 12 ordered post-publish assertions
  (manifest, staging absent, attestation valid, ledger hashes non-null +
  matching, ``.NO_LLM_ZONE`` sentinel, no quarantine, PHI-absence sweep,
  determinism, required-form coverage, lock absent, status.json).
- ``status``: prints scope banner + exit-code table.

Exit codes:

- ``0``: ok
- ``2``: manifest mismatch (missing required / unknown / reject)
- ``3``: audit ledger hash null or sentinel missing
- ``4``: quarantine directory non-empty
- ``5``: verifier assertion failed
- ``6``: needs-advice (paused — operator inspection required)
- ``7``: destruction incomplete
- ``8``: partial publish; held forms need human review

Scope: HIPAA Safe Harbor identifiers per ``phi_scrub.yaml`` + project patterns in
``phi_patterns.py``. Out of scope (operator responsibility): DPDPA §16 cross-border
egress, §12 right-to-erase, §8(6) breach notification, ICMR l-diversity gate.
Temp removal: operational untraceability (APFS COW acknowledged in destruction
attestation; not forensic erasure).

Cross-LLM canonical docs: :doc:`developer_guide/extract_to_llm_source`.

Release Note Rules
------------------

* Write for the affected audience, not from the commit history.
* Put the newest entries first.
* Use ISO dates when an entry moves from ``Unreleased`` to a released
  version.
* Group entries under ``Added``, ``Changed``, ``Fixed``, ``Removed``,
  ``Deprecated``, or ``Security``.
* Link to the relevant Sphinx page when a reader needs detail.
