Production Readiness
====================

This page is the release and deployment runbook for a controlled
single-study RePORT AI Portal instance. It does not replace study-team
validation of real data, but it defines the technical controls required
before operators treat a build as production-ready.

Scope
-----

RePORT AI Portal is local-first and single-study-focused. The default
supported production posture is:

* one study selected by ``STUDY_NAME``;
* one reviewed snapshot baseline under ``data/snapshots/{STUDY_NAME}/``;
* the live assistant reading only ``output/{STUDY_NAME}/trio_bundle/`` and
  ``output/{STUDY_NAME}/agent/``;
* no public unauthenticated access.

Production access for more than one user must sit behind an explicit
network and authentication boundary. Streamlit is the application server,
not the public security perimeter.

Release Gate
------------

Run the full release gate from a clean checkout:

.. code-block:: bash

   make release-check

This expands to:

.. code-block:: text

   verify → typecheck → test-all → docs-ci → security

The gate must pass before tagging or deploying. ``docs-ci`` includes the
warnings-as-errors Sphinx build and external link check. ``security`` runs
the dependency vulnerability audit. A dependency that cannot be audited
because it is the local project package is acceptable; third-party
vulnerability findings are not.

The release workflow must tag immutable releases as ``vX.Y.Z`` and attach
build artifacts to the GitHub Release. Deployments should pin to a tag, not
to a moving branch.

Deployment Boundary
-------------------

For local workstation use, run:

.. code-block:: bash

   make chat

The checked-in Streamlit configuration binds to ``127.0.0.1:8501`` with
CORS and XSRF protection enabled. Do not disable these settings to make a
proxy work; fix the proxy configuration instead.

For shared use, place the app behind a reverse proxy that provides:

* HTTPS/TLS termination;
* authentication before traffic reaches Streamlit;
* an allow-list or VPN/private-network boundary where appropriate;
* WebSocket proxying for Streamlit sessions;
* security headers at the proxy layer.

Production services must set ``REPORT_AI_AUTH_MODE=proxy`` and a long random
``REPORT_AI_PROXY_SHARED_SECRET`` through the deployment secret store. The
proxy must set both ``X-Forwarded-User`` and
``X-Report-AI-Proxy-Secret``. Missing or mismatched values stop the app before
the PHI-capable UI renders.

The repository includes starting templates:

* ``deploy/nginx/report-ai-portal.conf.example`` — Nginx reverse proxy
  with OAuth2 Proxy hook, TLS redirect, WebSocket forwarding, and security
  headers.
* ``deploy/systemd/report-ai-portal.service.example`` — Linux service
  unit with narrow writable paths and process hardening.
* ``deploy/systemd/report-ai-portal-healthcheck.*.example`` — timer-driven
  healthcheck that restarts the service when ``/_stcore/health`` fails.

Review the examples before use. Replace hostnames, certificate paths,
service users, writable paths, OAuth configuration, and CSP reporting
endpoints for the deployed environment.

Security Headers
----------------

The proxy should set browser security headers following the
`OWASP Secure Headers Project <https://owasp.org/www-project-secure-headers/>`_.
At minimum:

* ``Strict-Transport-Security`` on HTTPS deployments;
* ``X-Content-Type-Options: nosniff``;
* ``X-Frame-Options: DENY`` or an equivalent ``frame-ancestors`` CSP;
* ``Referrer-Policy``;
* ``Permissions-Policy`` denying unused browser capabilities;
* a tested ``Content-Security-Policy``.

Streamlit uses inline runtime assets, so start CSP in
``Content-Security-Policy-Report-Only`` mode, inspect reports while using
the full wizard and chat workflow, then promote a verified policy to
enforcement. Do not ship an untested enforcing CSP.

Monitoring
----------

Set ``LOG_FORMAT=json`` and ``LOG_DIR`` for deployed services. Monitor:

* service start, stop, restart, and non-zero exit events;
* ``PHI log redactor NOT installed`` warnings;
* pipeline failures and preserved ``tmp/{STUDY_NAME}/`` staging trees;
* snapshot restore failures;
* hosted LLM API errors and provider fallback events;
* dependency-audit failures in CI;
* unexpected reads or writes rejected by zone guards.

Alerting should page an operator for PHI-control failures, repeated
pipeline failures, or any public exposure of the Streamlit port without
the proxy/auth boundary.

Backups and Restore
-------------------

Back up only intentional durable state:

* ``data/raw/{STUDY_NAME}/`` if the study team permits raw-data backup;
* ``data/snapshots/{STUDY_NAME}/`` after human review;
* ``output/{STUDY_NAME}/audit/`` for lineage and compliance evidence;
* ``output/{STUDY_NAME}/agent/conversations/`` if conversation retention is
  approved;
* the sidecar PHI key at ``config.PHI_KEY_PATH``.

Do not back up ``.venv/``, ``tmp/``, ``.pytest_cache/``, ``.mypy_cache/``,
``.ruff_cache/``, or generated docs build output.

Backups that contain raw data, snapshots, audit files, conversations, or
the PHI key must be encrypted at rest and access-controlled. The PHI key
must be backed up separately from raw data when policy requires separation
of duties.

Restore drills are mandatory before production use:

1. restore the PHI key;
2. restore ``data/snapshots/{STUDY_NAME}/``;
3. run ``make restore-study``;
4. launch ``make chat``;
5. confirm the assistant reads the restored ``trio_bundle/`` and not
   ``data/snapshots/`` directly.

Run the non-destructive automated drill before hand-off:

.. code-block:: bash

   make restore-drill

Secret and Key Rotation
-----------------------

Hosted LLM API keys are provider secrets. Rotate them through the provider
console and update only the deployment secret store or local session input.
Never commit them.

The PHI HMAC key is different: it defines stable pseudonyms and shifted
dates. Rotating it changes derived identifiers. A PHI-key rotation requires:

1. stop the app and pipeline jobs;
2. archive the old key according to study policy;
3. create the replacement key through the developer/operator path;
4. run a full re-ingestion from raw data;
5. rebuild and review the snapshot baseline;
6. document the rotation in the study operations log.

Do not mix artifacts generated with different PHI keys in one reviewed
snapshot.

Incident Response
-----------------

For suspected PHI exposure, leaked credentials, public app exposure, or
incorrect bundle publication:

1. stop the service or block access at the proxy;
2. preserve logs, audit files, and the current ``output/{STUDY_NAME}/`` tree;
3. rotate hosted API keys if they may have been exposed;
4. quarantine the affected ``trio_bundle/`` and snapshot baseline;
5. identify whether raw, staging, audit, snapshot, or agent zones were
   exposed;
6. notify the PI/privacy owner under the study's IRB/IEC incident process;
7. rebuild from raw data only after the root cause is fixed and reviewed;
8. record the corrective action before restoring service.

Operational Non-Negotiables
---------------------------

* Never expose Streamlit directly to the internet.
* Never disable CORS or XSRF protection in production.
* Never let the LLM read ``data/raw/``, ``tmp/``, ``audit/``, or
  ``data/snapshots/`` directly.
* Never treat a snapshot as valid until the study team has reviewed it.
* Never rotate the PHI key without full re-ingestion.
* Never deploy a build that fails ``make release-check``.

External References
-------------------

* `Streamlit configuration <https://docs.streamlit.io/develop/api-reference/configuration/config.toml>`_
* `OWASP Secure Headers Project <https://owasp.org/www-project-secure-headers/>`_
