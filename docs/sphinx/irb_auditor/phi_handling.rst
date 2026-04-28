PHI Handling
============

This page states the privacy posture for IRB, IEC, and audit review.
It is not legal advice and does not replace site counsel, the study
protocol, or IRB/IEC approval.

Purpose
-------

The portal supports India-USA clinical research use where source data
may contain PHI, sensitive personal data, Indian identifiers, and
research participant information. The system is designed to minimize
disclosure risk before an AI assistant can answer study questions.

What Is Handled
---------------

The source material can include:

* study datasets and data dictionaries,
* annotated study PDFs,
* direct identifiers such as names, subject IDs, contact details,
  government IDs, precise addresses, and exact dates,
* quasi-identifiers such as age, sex, location, household composition,
  and clinical subgroups,
* clinical variables needed for approved research analysis.

The published assistant surface must contain only scrubbed study data,
agent state, and aggregate or privacy-checked answers.

Where PHI Can Exist
-------------------

.. list-table::
   :header-rows: 1
   :widths: 24 32 44

   * - Location
     - PHI posture
     - Reviewer meaning
   * - ``data/raw/{STUDY}/``
     - Presumed PHI-bearing
     - Source files are for extraction only. The assistant must not
       read this location.
   * - ``tmp/{STUDY}/``
     - Temporary PHI-bearing staging
     - Extraction, scrub, and cleanup run here. On successful completion
       the staging tree is securely removed.
   * - ``output/{STUDY}/trio_bundle/``
     - Published scrubbed bundle
     - This is the primary assistant read surface after the PHI scrub.
   * - ``output/{STUDY}/agent/``
     - Assistant-owned state
     - Chat and analysis artifacts must pass PHI redaction before they
       are persisted.
   * - ``output/{STUDY}/audit/``
     - Counts-only evidence
     - Audit files record counts, hashes, and lineage. The assistant is
       blocked from reading this location.
   * - ``data/snapshots/{STUDY}/``
     - Human-reviewed scrubbed baseline
     - Used only to overwrite a failed or incomplete trio bundle when a
       reviewed baseline is available. The assistant must not read this
       location directly.

How PHI Is Protected
--------------------

Source isolation
   Raw source files stay in the source folder. Extraction code reads
   them, but the assistant read validator rejects raw, staging, audit,
   and snapshot paths.

Eight-action scrub
   Staged datasets are scrubbed before publication. The scrub can keep
   approved clinical fields, drop direct identifiers, convert birthdate
   to safer date handling, cap age over 89, generalize categories,
   suppress small cells, shift dates by subject, and HMAC-pseudonymize
   IDs.

Date handling
   Default posture follows a de-identification approach: precise dates
   are not exposed as raw calendar dates. When dates are needed for an
   approved Limited Dataset use, each subject's dates are shifted by a
   consistent secret offset so intervals remain useful while actual
   calendar dates are obscured.

Identifier handling
   Direct identifiers and Indian government identifiers are dropped or
   pseudonymized. HMAC pseudonyms preserve longitudinal linkage without
   storing a reversible identifier map in the published bundle.

Small-cell protection
   Row-level assistant results must pass k-anonymity and l-diversity
   checks. Results that are too narrow are suppressed or reduced to an
   aggregate-safe response.

PDF handling
   PDFs are treated as PHI-bearing by default. The preferred path
   extracts text locally, redacts PHI before any LLM call, re-scrubs
   the LLM response, and falls back to the reviewed snapshot baseline if
   the LLM tier cannot be used. The legacy raw-PDF external path is
   refused unless a PHI-free PDF attestation and explicit environment
   flag are both present.

Audit handling
   Audit files are counts-only. Lineage records hashes and run metadata
   so an auditor can verify what was processed without reading raw PHI.

Why These Controls Exist
------------------------

The controls are intended to:

* reduce participant re-identification risk,
* prevent raw PHI from reaching hosted LLM providers,
* preserve scientific utility for approved cohort and longitudinal
  analysis,
* give IRB/IEC reviewers auditable evidence without exposing raw data,
* make privacy regressions fail tests before a release is accepted.

Jurisdiction Alignment
----------------------

India
   The posture aligns with ICMR ethical expectations for privacy,
   confidentiality, consent scope, and protection of research
   participants. It also treats digital personal data and sensitive
   health data conservatively under India's DPDPA/DPDP Rules framework,
   SPDI Rules while applicable, Aadhaar restrictions, and ABDM health-ID
   expectations.

USA
   The posture uses HIPAA Privacy Rule de-identification concepts as
   the USA research privacy anchor. Default handling follows Safe
   Harbor-style removal/generalization of direct identifiers. Limited
   Dataset behavior is opt-in only and requires a documented approval or
   Data Use Agreement. Any covered-entity research disclosure still
   requires the applicable IRB/Privacy Board authorization, waiver, or
   other permitted basis.

India-USA Research Use
   Because the study context spans India and USA research review, the
   system applies the stricter practical posture where controls overlap:
   source isolation, identifier removal, date protection, small-cell
   suppression, auditability, and explicit approvals for any mode that
   retains more identifiable detail.

Primary Authority Links
-----------------------

* HHS HIPAA de-identification guidance:
  https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html
* HIPAA Privacy Rule research guidance:
  https://www.hhs.gov/hipaa/for-professionals/special-topics/research/index.html
* 45 CFR 164.514:
  https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.514
* ICMR National Ethical Guidelines, 2017:
  https://www.icmr.gov.in/icmrobject/custom_data/pdf/resource-guidelines/ICMR_Ethical_Guidelines_2017.pdf
* Project reference list:
  :doc:`../developer_guide/references`
