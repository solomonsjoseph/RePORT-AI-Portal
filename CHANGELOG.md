# Changelog

User-facing release notes live in `docs/sphinx/release_notes.rst`.

## Unreleased

- Fixed local ``make chat`` startup when another Streamlit process already
  owns port 8501.
- Fixed production/proxy startup when the deployment uses an explicit or
  default study name without raw study input mounted at import time.
- Hardened production runtime controls for PHI log redaction, request rate
  limiting, CSP enforcement, direct virtualenv service execution, and
  study-name resolution.
- Added production auth-boundary checks, healthcheck deployment templates,
  release tagging, root security/license files, restore-drill automation, and
  production smoke checks.
