Production Backlog
==================

This page stores production hardening items that are useful but not required
for the current release gate. Revisit it during release planning.

Supply Chain
------------

* Add Dependabot or Renovate for Python dependencies and GitHub Actions.
* Emit a CycloneDX or SPDX SBOM on each GitHub Release.
* Decide whether ``pip-audit`` findings may ever use an allow-list, or keep
  the current fail-closed policy and document it explicitly.
* Add CODEOWNERS for security-sensitive surfaces under ``scripts/security/``,
  ``deploy/``, and IRB/auditor documentation.
* Add issue and pull request templates, including a private security triage
  path for suspected PHI exposure.

Runtime Resilience
------------------

* Add hosted-LLM retry/backoff and circuit-breaker behavior for OpenAI,
  Anthropic, Google, and NVIDIA provider calls.
* Add per-session token and estimated-cost ceilings with operator alerts for
  runaway agent loops.
* Add provider-side spend alarms and document who owns them.
* Add load and latency-regression checks with a concrete p95 target.

Observability
-------------

* Ship an example remote log/error sink configuration such as Loki/Promtail or
  Sentry, with the exact event names operators should alert on.
* Roll up telemetry JSONL into daily usage, token, cost, and anomaly summaries.
* Surface PHI redactor internal exceptions as a metric without leaking raw
  event text.

Data Retention
--------------

* Add a conversation retention command with max-age and max-count controls.
* Decide whether reviewed snapshots should remain single-slot or rotate by
  timestamp before overwrite.

Deployment Packaging
--------------------

* Add an OCI image for immutable deployment where operators cannot rely on
  ``uv sync`` against live package indexes.
* Add a data-flow diagram in ``docs/sphinx/irb_auditor/`` showing browser,
  proxy, Streamlit, local files, and hosted LLM provider egress.
* Define SLO, uptime, and error-budget targets for deployments that need a
  support contract.
