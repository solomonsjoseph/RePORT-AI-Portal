Attestations
============

The default posture does not require the attestation below. It is required
only when the study team enables a higher-risk mode.

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
