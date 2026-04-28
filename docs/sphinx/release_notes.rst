Release Notes
=============

Release notes are written for people who need to know what changed
without reading commits. Keep entries short, current, and grouped by
impact.

Unreleased
----------

Added
~~~~~

* Added this release-notes page to the Sphinx documentation.
* Added a contribution rule that every pull request should include a
  user-readable release note unless the change is purely internal and
  has no operator, reviewer, developer, or user impact.

Changed
~~~~~~~

* Reworked the GitHub README as a minimal entry point that sends readers
  to Sphinx for setup, IRB/auditor evidence, and developer detail.
* Consolidated the Sphinx audience routing into the documentation
  landing page.
* Moved the IRB/auditor profile into Sphinx and removed the old
  standalone Markdown dossier.

Release Note Rules
------------------

* Write for the affected audience, not from the commit history.
* Put the newest entries first.
* Use ISO dates when an entry moves from ``Unreleased`` to a released
  version.
* Group entries under ``Added``, ``Changed``, ``Fixed``, ``Removed``,
  ``Deprecated``, or ``Security``.
* Link to the relevant Sphinx page when a reader needs detail.
