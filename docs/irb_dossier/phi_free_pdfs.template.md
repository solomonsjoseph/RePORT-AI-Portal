# PHI-free PDF attestation — template

Copy this file to ``authorities/phi_free_pdfs.md`` (at the repository
root) and fill in the blanks before setting
``REPORTALIN_PDF_PHI_FREE=1``. The pipeline's PDF-extraction gate
requires both the env flag AND a non-empty file at that path.

The attestation is version-controlled, survives operator handoffs, and
is the single artefact the IRB dossier references when a reviewer asks
"who approved sending these PDFs to the external LLM?".

---

## Attestation

**Reviewed by:**  <name, role, institution>

**Reviewed at:**  <UTC timestamp, e.g. 2026-04-23T14:30:00Z>

**Files reviewed:**  data/raw/{STUDY}/annotated_pdfs/*.pdf
                    (list individual filenames if selective)

**Review procedure:**  Opened each PDF in a viewer and scanned for:

* Example subject IDs (real or placeholder)
* Example Aadhaar / PAN / voter / passport / phone numbers
* Concrete event dates that could identify a subject
* Staff signatures, initials, or names in footers / margins
* Scanner burn-in from a previously filled-in copy
* Version-control watermarks containing personal names

**Verified absent:**  Check each box after confirming the category is
not present in any reviewed file:

- [ ] No example subject IDs (real or placeholder)
- [ ] No example government-ID values
- [ ] No example phone / email / address values
- [ ] No concrete event dates for any subject
- [ ] No staff signatures or initials
- [ ] No scanner burn-in from filled-in copies
- [ ] No version-control watermarks with personal names

**Declaration:**  These PDFs are verified PHI-free. I authorise the
pipeline to send them to the configured external LLM provider for
structured-variable extraction.

**Signature:**  <signature, or equivalent commit-sign attestation>
