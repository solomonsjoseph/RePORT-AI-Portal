Source Truth Build
==================

This page is for maintainers and agents that need to rebuild reviewed
Source Truth YAML for one study. After reading it, you should know which
commands produce source packs, where LLM/manual authoring is allowed, and
which deterministic gates must pass before a YAML reaches
``llm_source/``.

The printed PDF is the clinical authority. Dataset workbooks are used
for row-1 headers only during Source Truth authoring; row 2 and later
are not read for this workflow.

Prerequisites
-------------

.. list-table::
   :header-rows: 1

   * - Item
     - Check
   * - Annotated PDF
     - ``data/raw/{STUDY}/annotated_pdfs/{FORM}.pdf``
   * - Dataset workbook
     - ``data/raw/{STUDY}/datasets/{FORM}.xlsx`` or ``.csv``
   * - Ghostscript
     - ``gs --version``
   * - uv
     - ``uv --version``
   * - Runtime output directory
     - ``output/{STUDY}/llm_source/source_truth/``

Batch Runtime Build
-------------------

Use the batch command for a normal runtime rebuild:

.. code-block:: bash

   make build-llm-source STUDY=Indo-VAP

That command:

1. creates source packs for PDF-backed forms,
2. generates conservative lean YAML candidates under ``/tmp``,
3. verifies each candidate,
4. promotes passing YAMLs to
   ``output/Indo-VAP/llm_source/source_truth/``, and
5. runs the main pipeline to publish dictionary mappings,
   PHI-scrubbed dataset JSONL, audit ledgers, lineage, and the output
   signpost.

Use ``make rebuild-llm-source STUDY=Indo-VAP`` when you want to remove
generated ``llm_source/`` and study staging first. It preserves audit
manifests and ``output/{STUDY}/agent/``.

Single-Form Stage Flow
----------------------

Stage 0: source pack
~~~~~~~~~~~~~~~~~~~~

Run the deterministic source-pack extractor:

.. code-block:: bash

   make sot-source-pack STUDY=Indo-VAP FORM=6_HIV

Equivalent direct CLI:

.. code-block:: bash

   python -m scripts.source_truth.study_intake --study Indo-VAP --form 6_HIV

Expected outputs:

* ``/tmp/sot_source_pack_6_HIV.json`` with the dataset header array,
  PDF SHA-256, render list, and first-render compatibility alias.
* ``/tmp/sot_render_6_HIV/*.page-001.png`` and following pages, rendered
  at 600 DPI for visual review.

Stop if either output is missing. Check Ghostscript, source paths, and
whether the PDF is password-protected or truncated.

Stages 1-3: LLM/manual authoring
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

High-assurance authoring requires LLM or manual reasoning over the
source pack and rendered PDF pages. Runtime rebuilds may use the
script-backed candidate generator, but those candidates still require
the same verifier and diff gates before promotion.

For manual or LLM authoring:

1. Read ``skills/sot-lean-generator/references/exhaustive_yaml_rules.md``.
   Write the exhaustive YAML draft from the source pack and the 600 DPI
   page renders.
2. Run five visual sweep iterations over every rendered page. Correct
   widget type, field label, value-set, section, and skip-logic
   mismatches. If the render is ambiguous, pause for human review.
3. Read ``skills/sot-lean-generator/references/lean_yaml_rules.md``.
   Trim the exhaustive draft to the lean schema and write
   ``/tmp/6_HIV_lean.yaml``.

All LLM tools use the same rule files and the same verifier. A
tool-specific skill can point to this flow, but the rules and command
surface are not tool-specific.

Stage 4: verify
~~~~~~~~~~~~~~~

Run the deterministic verifier:

.. code-block:: bash

   make sot-verify STUDY=Indo-VAP FORM=6_HIV

By default this validates ``/tmp/6_HIV_lean.yaml``. Pass
``CANDIDATE=/path/to/file`` to override.

.. list-table::
   :header-rows: 1

   * - Exit code
     - Meaning
     - Action
   * - 0
     - All checks passed
     - Continue to validation and promotion.
   * - 1
     - Content or validation failure
     - Fix the candidate and rerun.
   * - 2
     - Source-pack SHA mismatch
     - Re-run Stage 0, then redo authoring.
   * - 3
     - Script gap
     - Stop and ask for human review.

Stage 4.5: property validator and diff-against-gold
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run all deterministic gates together:

.. code-block:: bash

   make sot-validate STUDY=Indo-VAP FORM=6_HIV

This requires ``/tmp/sot_source_pack_6_HIV.json`` and validates
``/tmp/6_HIV_lean.yaml`` by default. It chains the verifier, property
validator, and diff-against-gold check. Any failure blocks promotion.

To inspect gold diffs directly:

.. code-block:: bash

   uv run --all-groups python scripts/source_truth/diff_against_gold.py \
     --study Indo-VAP --form 6_HIV \
     --candidate /tmp/6_HIV_lean.yaml

Anchored calibration gold lives at ``data/SoT/{STUDY}/``. Runtime YAMLs
under ``output/{STUDY}/llm_source/source_truth/`` are generated outputs;
they are never silently copied over anchored gold.

Stage 5: promote
~~~~~~~~~~~~~~~~

Promote only after all validation gates pass:

.. code-block:: bash

   cp /tmp/6_HIV_lean.yaml \
     output/Indo-VAP/llm_source/source_truth/6_HIV_policy.lean.yaml

The canonical runtime output path is:

.. code-block:: text

   output/{STUDY}/llm_source/source_truth/{FORM}_policy.lean.yaml

Escalation Rules
----------------

Stop and ask for human review when:

* a widget shape, field label, option, or skip condition is ambiguous in
  the 600 DPI render;
* the verifier exits with code 3;
* the verifier exits with code 2 more than once after Stage 0 is rerun;
* the printed form, annotations, and dataset headers contradict each
  other in a way that changes clinical meaning;
* a candidate would change anchored gold without an approved anchor or
  re-anchor workflow.

Related Source Files
--------------------

.. list-table::
   :header-rows: 1

   * - Path
     - Role
   * - ``scripts/source_truth/study_intake.py``
     - Cross-LLM CLI wrapper for source-pack extraction.
   * - ``skills/sot-lean-generator/scripts/extract_sources.py``
     - Stage 0 implementation.
   * - ``skills/sot-lean-generator/scripts/check_lean_policy.py``
     - Stage 4 verifier.
   * - ``scripts/source_truth/diff_against_gold.py``
     - Gold regression diff gate.
   * - ``skills/sot-lean-generator/references/exhaustive_yaml_rules.md``
     - Stage 1 authoring rules.
   * - ``skills/sot-lean-generator/references/lean_yaml_rules.md``
     - Stage 3 lean schema rules.
