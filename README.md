# RePORT AI Portal

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Documentation](https://img.shields.io/badge/docs-sphinx-blue.svg)](https://solomonsjoseph.github.io/RePORT-AI-Portal/)
[![Status](https://img.shields.io/badge/status-beta-blue.svg)](https://github.com/solomonsjoseph/RePORT-AI-Portal)

RePORT AI Portal is a local-first assistant for one clinical research
study. It helps a study team load local source files, publish a
PHI-scrubbed study bundle, and ask grounded questions through a chat
interface.

The user docs stay brief: what it does, how it helps, how to set it up,
and how to run it. Architecture, source files, tests, and implementation
details live in the developer docs.

**Documentation:** <https://solomonsjoseph.github.io/RePORT-AI-Portal/>

## Who It Helps

- **Clinical researchers** ask cohort and variable questions without
  waiting for a custom data cut.
- **Data managers** prepare one study bundle and reduce repeated manual
  exports.
- **PIs and reviewers** get output folders and audit files they can
  inspect without opening raw subject data.
- **Developers** use the developer guide for internals, tests, and
  contribution workflow.

## What It Does

- Reads one local study from `data/raw/{STUDY_NAME}/`.
- Publishes a scrubbed study bundle under `output/{STUDY_NAME}/`.
- Opens a web chat UI for questions about the published bundle.
- Produces audit files for run review and troubleshooting.
- Supports local Ollama or hosted LLM providers.

## Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
cd RePORT-AI-Portal
uv sync --all-groups
```

Verify:

```bash
uv run python -c "import scripts; print('Installation successful')"
```

## Prepare a Study

Put one study under `data/raw/{STUDY_NAME}/`:

```text
data/raw/Indo-VAP/
├── datasets/
├── data_dictionary/
└── annotated_pdfs/        # optional
```

Set the study name if needed:

```bash
export STUDY_NAME=Indo-VAP
```

Create the local PHI key once per machine:

```bash
python -m scripts.security.phi_scrub bootstrap-key
```

## Choose a Model Provider

Recommended local setup:

```bash
export LLM_PROVIDER=ollama
export LLM_MODEL=qwen3:8b
```

Hosted provider example:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_MODEL=claude-opus-4-7
```

See the configuration guide for OpenAI, Google, and PDF-related settings:
<https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/configuration.html>

## Run

Launch the web UI:

```bash
make chat
```

Then choose a provider and either:

- click **Load Study** to process raw study files; or
- click **Use Existing Study** if a published bundle already exists.

Run only the pipeline:

```bash
make pipeline
```

## Output

After loading a study, review:

```text
output/{STUDY_NAME}/
├── trio_bundle/       # scrubbed bundle used by the assistant
├── audit/             # run evidence and troubleshooting files
├── agent/             # chat state and generated analysis
└── README.md          # local output summary
```

## Where to Read More

- User guide: <https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/>
- Quick start: <https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/quickstart.html>
- FAQ: <https://solomonsjoseph.github.io/RePORT-AI-Portal/user_guide/faq.html>
- Developer guide: <https://solomonsjoseph.github.io/RePORT-AI-Portal/developer_guide/>
- IRB dossier: `docs/irb_dossier/`

## Development

Developer detail intentionally stays out of this README. Use the
developer guide for architecture, source layout, testing, release
process, and contribution rules.

Common checks:

```bash
make test
make docs-quality
```

## Support

- Issues: <https://github.com/solomonsjoseph/RePORT-AI-Portal/issues>
- Documentation: <https://solomonsjoseph.github.io/RePORT-AI-Portal/>

---

**Version**: 0.21.1 | **Status**: Beta
