# Security Policy

## Reporting a Vulnerability

Do not open a public issue for suspected PHI exposure, credential leakage, or
an authentication bypass.

Email the maintainer listed in `pyproject.toml` with:

- affected version or commit SHA;
- deployment mode, excluding secrets and PHI;
- reproduction steps;
- observed impact;
- whether PHI, raw study files, API keys, or audit artifacts may be exposed.

The maintainer should acknowledge security reports within 3 business days and
coordinate disclosure after the affected study owner and privacy lead have been
notified.

## Supported Versions

Only the latest tagged release and the current `main` branch receive security
fixes. Deployments handling study data should pin to a tag, not an arbitrary
commit.
