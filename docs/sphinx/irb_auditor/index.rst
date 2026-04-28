IRB/Auditor Profile
===================

This profile is only for IRB, IEC, privacy, and audit review. It states
what PHI is handled, where it can exist, how it is protected, and why
those controls align with India and USA research privacy expectations.

Use this profile to review the privacy posture without reading
developer implementation notes or general user setup instructions.

Review Path
-----------

1. Read :doc:`phi_handling` for the PHI flow and jurisdiction alignment.
2. Read :doc:`conformance` for the claim-to-evidence control table.
3. Read :doc:`attestations` for the two approvals that operators must
   file before higher-risk modes are used.

What This System Does
---------------------

RePORT AI Portal is a local-first assistant for one clinical research
study. It converts local study inputs into a PHI-scrubbed trio bundle
and lets approved researchers ask questions from that scrubbed bundle.

What This Profile Covers
------------------------

* India and USA research privacy alignment for Indo-VAP-style clinical
  research use.
* PHI and personal-data handling from source files to assistant answer.
* De-identification, pseudonymization, date shifting, small-cell
  suppression, and LLM boundary controls.
* Evidence an IRB, IEC, privacy reviewer, or auditor can request.

What This Profile Does Not Cover
--------------------------------

* Code architecture, contributor workflow, or build history.
* Model-provider setup beyond PHI handling implications.
* General user instructions for running the portal.

For those audiences, use :doc:`../user_guide/index` or
:doc:`../developer_guide/index`.

Contents
--------

.. toctree::
   :maxdepth: 2

   phi_handling
   conformance
   attestations
