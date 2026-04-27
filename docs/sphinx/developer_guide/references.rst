References
==========

**What.** Every regulation, standard, paper, and external resource cited
in the RePORT AI Portal codebase or IRB dossier, collected in one place
with URLs and a line on which pillar / module they back.

**Why.** Regulatory traceability is a developer concern. If you're
touching the PHI scrubber's catalog or the agent-boundary gate, you
should be able to reach the primary source for HIPAA §164.514(b)(2)(i)
or ICMR §11.7 from the docs in one click. If you're adding a new rule
class, you should know which regulation the rule answers.

**How.** Sorted by concern area (regulation / standard / technique /
benchmark). Each entry includes a short "used for" line pointing at
the module or pillar the reference backs.

.. contents:: On this page
   :local:
   :depth: 2

Primary Regulations
-------------------

HIPAA Privacy Rule — §164.514 De-identification
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Full text.** https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.514
* **HHS guidance.** https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html
* **What we use it for.** §164.514(b)(2)(i)(A–R) is the reference list
  of 18 identifier classes that the :doc:`phi_architecture` catalog
  maps to. §164.514(b)(2)(i)(C) specifically backs the age-over-89
  cap and the date-precision rules.

DPDPA 2023 — Digital Personal Data Protection Act
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf
* **DPDP Rules 2025 (notified 13 Nov 2025).** https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf
* **What we use it for.** India's primary personal-data regulation.
  §2(t) defines "personal data"; §9 governs children's data;
  §8 governs data-fiduciary obligations (minimization, retention,
  accuracy). The PHI catalog's drop rules for Indian government IDs
  (Aadhaar / ABHA / PAN / voter / PM-JAY) anchor to DPDPA.

SPDI Rules 2011 (under IT Act §43A)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://www.indiacode.nic.in/handle/123456789/1362
* **What we use it for.** Still the in-force regulation until DPDPA's
  substantive provisions kick in. Rule 3 defines Sensitive Personal
  Data or Information (SPDI); health data and biometric data are
  explicitly covered.

Aadhaar Act 2016 — §29 restrictions on sharing identity information
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://uidai.gov.in/images/Aadhaar_Act_2016_as_amended.pdf
* **What we use it for.** Justifies the aggressive drop rule for any
  Aadhaar-shaped identifier (12-digit, ``1234 5678 9012``) in both
  the scrub catalog and the phi_patterns BLOCKING regex list.

ICMR National Ethical Guidelines for Biomedical & Health Research (2017)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://main.icmr.nic.in/sites/default/files/guidelines/ICMR_Ethical_Guidelines_2017.pdf
* **What we use it for.** §11 (confidentiality and community-level
  privacy) backs the suppress_small_cell action for household-contact
  counts; §11.7 explicitly mandates k-anonymity-style controls for
  cohort studies; §5 backs date-precision requirements.

ABDM Health Data Management Policy (NHA)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://abdm.gov.in/publications/health_data_management_policy
* **What we use it for.** Governs ABHA (health ID) records. Referenced
  in the PHI catalog for any ``ABHA`` / ``health_id`` column shape.

RePORT India Common Protocol
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Project site.** https://www.reportindia.org
* **What we use it for.** The parent study protocol under which
  Indo-VAP runs. Dictates the 72-hour IRB notification window for PHI
  breaches (documented in the breach-response runbook stub at
  ``docs/irb_dossier/breach_response_runbook.md``).

Standards & Frameworks
----------------------

NIST SP 800-188 — De-Identifying Government Datasets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-188.pdf
* **What we use it for.** §5.2 backs the integrity-chain requirement
  (SHA-256 of every raw input in every row's provenance + in the
  lineage manifest). §6.3-6.5 backs the AMBER transient-workspace
  hardening (mode 0700, zero-fill on teardown).

NIST SP 800-175B — Guideline for Using Cryptographic Standards
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://csrc.nist.gov/publications/detail/sp/800-175b/rev-1/final
* **What we use it for.** HMAC-SHA256 as a keyed-MAC construction;
  key-rotation semantics for the sidecar HMAC key.

NIST SP 800-53 (SI-7 Software, Firmware, and Information Integrity)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final
* **What we use it for.** SI-7 backs the per-run lineage manifest
  hash chain.

STROBE — Strengthening the Reporting of Observational Studies in Epidemiology
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://www.equator-network.org/reporting-guidelines/strobe/
* **What we use it for.** Reporting-fidelity requirements that drive
  the provenance-on-every-row design; specifically §6 (data sources +
  measurement) and §14 (descriptive data).

RECORD — REporting of studies Conducted using Observational Routinely-collected health Data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://www.record-statement.org
* **What we use it for.** Extension of STROBE for routinely-collected
  data (EHR, registry). §3 backs NA-preservation behaviour — clinical
  strings like "NR" / "NA" / "NK" must not be coerced to Python None
  during extraction.

CDISC SDTM / ODM — Clinical Data Interchange Standards
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Overview.** https://www.cdisc.org/standards/foundational/sdtm
* **ODM.** https://www.cdisc.org/standards/data-exchange/odm
* **What we use it for.** Origin / source traceability requirements
  answered by the provenance dict's ``source_file`` / ``sheet_name``
  / ``row_index`` fields; ODM variable-definition shape in the study
  dictionary.

FDA 21 CFR Part 11 — Electronic Records + Electronic Signatures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Text.** https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11
* **What we use it for.** §11.10(e) backs the audit-trail requirement
  (who / what / when for every transformation). The lineage manifest
  plus the per-row ``_provenance.pipeline_version`` + ``extraction_engine``
  fields satisfy this.

HHS Honest Broker guidance
~~~~~~~~~~~~~~~~~~~~~~~~~~

* **OHRP guidance.** https://www.hhs.gov/ohrp/regulations-and-policy/guidance/research-with-protected-health-information/index.html
* **What we use it for.** Canonical "honest broker" pattern that the
  four-tier architecture implements as code (raw → staging → published
  → agent).

Techniques
----------

SANT — Shift-And-Not-Truncate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Primary citation.** El Emam et al., "A method for managing
  re-identification risk from small geographic areas in Canada,"
  BMC Medical Informatics and Decision Making, 2010.
* **What we use it for.** Per-subject constant date offset so
  intra-subject intervals are preserved exactly — the
  :func:`scripts.security.phi_scrub.date_offset_days` algorithm.

k-anonymity — Sweeney 2002
~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Paper.** https://epic.org/wp-content/uploads/privacy/reidentification/Sweeney_Article.pdf
* **What we use it for.** Backs the
  :func:`scripts.security.kanon_gate.kanon_check` equivalence-class
  test at the agent boundary. Default k = 5 per ICMR §11.7.

l-diversity — Machanavajjhala et al. 2007
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Paper.** https://dl.acm.org/doi/10.1145/1217299.1217302
* **What we use it for.** Complement to k-anonymity for sensitive
  attribute homogeneity; relevant when a small equivalence class
  happens to all share the same outcome. As of v0.18.0
  (PR #13, Phase 3.B) we enforce **k-anon (k=5) AND l-diversity (l=2)**
  on every row-returning agent tool — see
  :func:`scripts.security.kanon_gate.l_diversity_check` and
  :func:`scripts.security.kanon_gate.guard_rows_with_kanon_and_ldiv`.

HMAC (RFC 2104)
~~~~~~~~~~~~~~~

* **Text.** https://datatracker.ietf.org/doc/html/rfc2104
* **What we use it for.** Keyed-hash-based pseudonymization of subject
  IDs (:func:`scripts.security.phi_scrub.pseudo_id`) and per-subject
  date offset derivation.

Benchmarks & Comparative Studies
--------------------------------

Microsoft Presidio benchmarks (2024-2025)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Paper / writeup.** https://microsoft.github.io/presidio/evaluation/
* **What we use it for.** Cited in :doc:`decisions` ADR-004 as evidence
  for the "rule+allowlist over Presidio" choice — 22.7% precision in
  mixed enterprise data, ~84% F1 on clinical notes.

John Snow Labs Clinical NER
~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Site.** https://www.johnsnowlabs.com
* **What we use it for.** Reference point for what commercial clinical
  NER can do (~98.6% F1). Not used at runtime — documented in ADR-004
  as a "what we gave up" note.

i2b2 / n2c2 de-identification shared tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Site.** https://portal.dbmi.hms.harvard.edu/projects/n2c2-nlp/
* **What we use it for.** Corpus used to benchmark clinical de-
  identification systems. Referenced in Stage-5 (Ollama NER)
  calibration notes.

Tools & Libraries Cited in Decisions
------------------------------------

pdfplumber
~~~~~~~~~~

* **Site.** https://github.com/jsvine/pdfplumber
* **What we use it for.** Long-term target for local-only PDF
  extraction (to replace the current external-API path under
  ADR-006). Not yet in the runtime.

Ollama
~~~~~~

* **Site.** https://ollama.com
* **What we use it for.** Local LLM runtime used by the agent. Target
  for Stage-5 narrative NER (see :mod:`scripts.security.phi_ner`).

Reading Order for a New Contributor
-----------------------------------

If you're new to the project and need to come up to speed:

1. Read :doc:`../user_guide/overview` for the pain narrative.
2. Read :doc:`phi_architecture` for the four-tier + 8-action story.
3. Read :doc:`decisions` in full — the Why answers are here.
4. Come back here as a reference when you need to justify or
   challenge an architectural choice.
5. Read the HIPAA §164.514(b)(2)(i) primary source and the NIST SP
   800-188 first three sections to ground the regulatory vocabulary.
