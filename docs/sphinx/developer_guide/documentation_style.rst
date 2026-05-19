Documentation Style
===================

Use this contract when adding or editing Sphinx pages. The goal is not
more prose. The goal is fewer stale pages, clearer entry points, and
documentation that helps each reader finish a task or make a decision.

Style Basis
-----------

The project uses a pragmatic blend of:

* Diataxis: separate tutorials, how-to guides, reference, and explanation.
* Google audience guidance: match vocabulary and detail to the reader's
  role and proximity to the system.
* Microsoft Learn voice: start with the customer's task, use everyday
  words, keep pages scannable.
* Google code-documentation practice: keep a small set of accurate docs
  alive and remove obsolete material.
* MDN accessibility guidance: use inclusive wording, descriptive links,
  and section names instead of visual directions.
* Sphinx structure: use cross-references, toctrees, autodoc, and warnings
  as errors through ``make docs-quality``.

Documentation Boundary
----------------------

The project has one durable documentation library and one public entry
point:

* ``README.md`` is the GitHub front door. Keep it short: what the
  project is, where each audience should go, a minimal quick start, and
  support links.
* ``docs/sphinx/`` is the documentation library. Put setup detail,
  privacy evidence, architecture, testing, operations, and contributor
  workflow here.
* ``docs/sphinx/release_notes.rst`` is the release-note source. Add a
  short entry for every pull request unless the change has no reader
  impact.

Do not add standalone Markdown packets or parallel documentation trees.
If a topic needs detail, add or update a Sphinx page and link to it from
the README only when it is a common entry point.

Root files such as ``AGENTS.md``, ``SECURITY.md``, and
``CHANGELOG.md`` are bootstrap or GitHub metadata surfaces. Keep them
short and point readers to Sphinx for durable instructions. Historical
plans, local skill shims, and generated outputs are not current
documentation unless their content has been promoted into
``docs/sphinx/``.

Profile-first Rule
------------------

Every new page must identify its reader in the first screen of content.
Use one of these reader groups unless the page has a narrower audience.

**User-facing pages** are for:

* clinical researchers
* data managers
* IRB or IEC reviewers
* site PIs and local operators

**Developer-facing pages** are for:

* pipeline developers
* agent/tool developers
* privacy and security reviewers
* maintainers and release reviewers
* documentation contributors

User-facing Page Pattern
------------------------

Use this structure for task pages in ``user_guide/``:

1. **Reader and outcome.** Name who the page is for and what they can do
   after reading it.
2. **Before you start.** List prerequisites, required files, and PHI
   posture assumptions.
3. **Steps.** Keep procedures direct. Split any procedure longer than 12
   steps into smaller sections.
4. **Expected result.** Show stable command output, file paths, or audit
   artifacts that prove success.
5. **Troubleshooting.** Give the next action for common failures.
6. **Next steps.** Link to the exact next page by title.

User-facing pages should use plain language first. Explain acronyms on
first use. Avoid module names unless the user must run a command or
inspect a file.

Developer-facing Page Pattern
-----------------------------

Use this structure for technical pages in ``developer_guide/``:

1. **Purpose.** State the system surface the page covers.
2. **Entry points.** Name source modules, commands, or configuration
   files.
3. **Invariants.** List the behavior that must not regress.
4. **Data flow or API contract.** Show inputs, outputs, and allowed side
   effects.
5. **Failure modes.** Describe how the system fails closed.
6. **Tests.** Name the verification command and the focused test module
   or test class.
7. **Change checklist.** List the code, docs, and audit artifacts that
   must stay synchronized.

Developer-facing pages can use source names and implementation detail,
but they must still start from the reader's task.

IRB-facing Page Pattern
-----------------------

IRB-facing pages must be evidence-first:

* Name the control.
* Name the PHI risk it reduces.
* Name the artifact or source module that implements it.
* Name the test, CI gate, or audit output that verifies it.
* State any residual risk plainly.

Do not bury a residual risk in a roadmap paragraph. Put it in the
follow-up register or the current status page.

Language Rules
--------------

Use these rules across the Sphinx tree:

* Use sentence case for new headings.
* Use active voice.
* Use "you" only when addressing the reader directly in a task.
* Avoid idioms and cultural references.
* Avoid visual directions and vague link text. Link to the section or
  page by name.
* Prefer short paragraphs and concrete nouns.
* Prefer exact file paths and commands over vague descriptions.
* Do not hard-code test counts unless a linter or generated artifact
  checks the count.
* Do not describe a historical PR as current behavior.
* Do not claim a security posture unless the page also names the
  enforcing code or audit artifact.

Freshness Checks
----------------

Run these checks before committing documentation:

.. code-block:: bash

   uv run python scripts/lint_doc_freshness.py
   make docs-quality
   make verify

``make docs-quality`` builds Sphinx with warnings treated as errors and
runs the documentation freshness linter. A documentation change is not
done until these gates pass.

Change Checklist
----------------

Before opening a documentation PR:

1. Confirm the page has one clear reader profile.
2. Confirm the first section states the outcome.
3. Confirm every command and path still exists.
4. Confirm every PHI or IRB claim names an implementation artifact or
   verification artifact.
5. Add or update :doc:`../release_notes` when the change affects users,
   operators, reviewers, or developers.
6. Check for new standalone docs:
   ``git ls-files '*.md' '*.rst' | grep -v '^docs/sphinx/'``.
   Any new non-Sphinx file should be a README, machine bootstrap,
   GitHub metadata file, skill artifact, or historical plan with no
   current-runbook claims.
7. Run the freshness, Sphinx, and verification gates.
