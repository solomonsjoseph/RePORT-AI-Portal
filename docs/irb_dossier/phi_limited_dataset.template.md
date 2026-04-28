# Limited Dataset attestation - template

Copy this file to `authorities/phi_limited_dataset.md` at the repository
root before setting `compliance_posture: limited_dataset` in
`scripts/security/phi_scrub.yaml`.

The scrubber requires this non-empty attestation before it preserves
birthdates or precise dates under the HIPAA Limited Dataset posture.

---

## Attestation

**Reviewed by:** <name, role, institution>

**Reviewed at:** <UTC timestamp, e.g. 2026-04-23T14:30:00Z>

**Study / dataset:** <study name and dataset version>

**Authorising protocol / DUA:** <IRB/IEC protocol, DUA, or approval identifier>

**Approved Limited Dataset fields:**

- [ ] Birthdate fields may be retained after per-subject date shifting.
- [ ] Event-date fields may be retained after per-subject date shifting.
- [ ] Direct identifiers remain dropped or pseudonymised by `phi_scrub.yaml`.
- [ ] Free-text narrative fields remain dropped unless separately approved.

**Review procedure:** Confirmed that the Limited Dataset posture is required for
the approved analysis and that recipients are bound by the cited DUA/protocol.

**Declaration:** I authorise the pipeline to run with
`compliance_posture: limited_dataset` for the study/dataset named above.

**Signature:** <signature, or equivalent commit-sign attestation>
