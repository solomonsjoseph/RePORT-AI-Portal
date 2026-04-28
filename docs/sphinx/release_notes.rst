Release Notes
=============

Release notes are written for people who need to know what changed
without reading commits. Keep entries short, current, and grouped by
impact.

Unreleased
----------

Added
~~~~~

* Added production fail-closed controls for PHI log redaction, app/proxy rate
  limiting, direct virtualenv service execution, and stricter production study
  resolution.
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

* Updated a sandbox regression test so its expected output no longer
  resembles a PHI phone-number pattern on Python 3.13.

Release Note Rules
------------------

* Write for the affected audience, not from the commit history.
* Put the newest entries first.
* Use ISO dates when an entry moves from ``Unreleased`` to a released
  version.
* Group entries under ``Added``, ``Changed``, ``Fixed``, ``Removed``,
  ``Deprecated``, or ``Security``.
* Link to the relevant Sphinx page when a reader needs detail.
