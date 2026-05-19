# RePORT AI Portal

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Documentation](https://img.shields.io/badge/docs-sphinx-blue.svg)](https://solomonsjoseph.github.io/RePORT-AI-Portal/)
[![Status](https://img.shields.io/badge/status-beta-blue.svg)](https://github.com/solomonsjoseph/RePORT-AI-Portal)

RePORT AI Portal is a local-first assistant for one clinical research
study. It helps a study team load local source files, publish a
PHI-scrubbed study bundle, and ask grounded questions from that bundle.

This README is only the front door. The full user, IRB/auditor, and
developer documentation lives in Sphinx. Local source files are under
`docs/sphinx/`; published docs are at:
<https://solomonsjoseph.github.io/RePORT-AI-Portal/>

## Start Here

| Need | Go to |
| --- | --- |
| Use the portal | [User guide](https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/) |
| Run the first study | [Quick start](https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/quickstart.html) |
| Configure models and study settings | [Configuration](https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/configuration.html) |
| Review PHI handling | [IRB/Auditor profile](https://solomonsjoseph.github.io/RePORT-AI-Portal/irb_auditor/) |
| Change or maintain code | [Developer guide](https://solomonsjoseph.github.io/RePORT-AI-Portal/developer_guide/) |

## Quick Start

Install `uv` for your platform:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then start the portal:

```bash
git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
cd RePORT-AI-Portal
make chat
```

`make chat` installs the web/AI dependencies it needs, launches the web
UI, and guides provider selection, study loading, PHI-key setup, and chat. For the
complete setup path, use the
[installation](https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/installation.html)
and
[quick start](https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/quickstart.html)
pages.

## Privacy Boundary

Treat source study files as PHI-bearing unless your study team has
verified otherwise. The assistant is designed to answer from the
published scrubbed bundle, not from raw source files. IRB, IEC, privacy,
and audit reviewers should start with the
[IRB/Auditor profile](https://solomonsjoseph.github.io/RePORT-AI-Portal/irb_auditor/).

## Development

Use the
[developer guide](https://solomonsjoseph.github.io/RePORT-AI-Portal/developer_guide/)
for architecture, testing, operations, production readiness, and contribution
workflow.

Common local checks:

```bash
make test
make docs-quality
```

## Support

Open an issue for bugs or documentation gaps:
<https://github.com/solomonsjoseph/RePORT-AI-Portal/issues>
