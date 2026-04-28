Attestations
============

The default posture does not require either attestation below. They are
required only when the study team enables a higher-risk mode.

PHI-Free PDF Attestation
------------------------

Required before setting ``REPORTALIN_PDF_PHI_FREE=1`` for the legacy
raw-PDF external provider path.

Store the completed attestation at ``authorities/phi_free_pdfs.md``.

Required content:

* reviewer name, role, and institution,
* UTC review timestamp,
* study name and PDF list reviewed,
* confirmation that no subject IDs, government IDs, phone numbers,
  emails, addresses, precise participant event dates, signatures,
  initials, scanner burn-in, or personal watermarks are present,
* approval for the configured external provider to process those PDFs,
* signature or commit-sign equivalent.

Declaration text:

.. code-block:: text

   I reviewed the listed PDFs and verified that they contain no PHI,
   personal identifiers, or participant-specific event details. I
   authorize use of REPORTALIN_PDF_PHI_FREE=1 for the named study and
   provider.

Limited Dataset Attestation
---------------------------

Required before setting ``compliance_posture: limited_dataset`` in
``scripts/security/phi_scrub.yaml``.

Store the completed attestation at
``authorities/phi_limited_dataset.md``.

Required content:

* reviewer name, role, and institution,
* UTC review timestamp,
* study and dataset version,
* IRB/IEC protocol, Data Use Agreement, or approval identifier,
* exact fields approved for Limited Dataset handling,
* confirmation that direct identifiers remain dropped or pseudonymized,
* confirmation that free-text narrative fields remain dropped unless
  separately approved,
* signature or commit-sign equivalent.

Declaration text:

.. code-block:: text

   I authorize Limited Dataset handling for the named study and dataset
   under the cited protocol or Data Use Agreement. Direct identifiers
   remain dropped or pseudonymized, and approved dates are shifted before
   publication.
