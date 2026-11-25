<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo_dark.svg" />
  <img src="assets/logo_light.svg" alt="SillyTherin" width="420" />
</picture>

### Build your own dataset from pure curiosity.

A playground for your own data pool, where every question you ask<br />
grows it a little deeper and every answer is yours to explore.

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/server-FastAPI-009688)](https://fastapi.tiangolo.com)
[![OpenRouter](https://img.shields.io/badge/models-OpenRouter-6566F1)](https://openrouter.ai)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

[Overview](#overview) · [Features](#features) · [Getting started](#getting-started) · [Configuration](#configuration) · [Security](#security) · [Development](#development)

</div>

---

## Overview

Data analysis usually means switching between a search engine, dataset portals, download scripts and a Jupyter notebook. SillyTherin brings these into a single chat. You ask a question; the assistant researches it on the web, finds and downloads a suitable dataset, and analyses it in a notebook that sits beside the conversation. Every step it takes is shown in the chat, so the work stays transparent and reproducible.

SillyTherin is designed for analysts, students and researchers who want to move from an idea to running code quickly, without setting up the tooling by hand.

## Features

| | |
|---|---|
| **Reasoning** | Choose a thinking level per chat and follow the model's reasoning as it streams in. |
| **Web research** | Searches the web and reads public pages, with each search and page shown as a step. |
| **Dataset discovery** | Finds datasets across Hugging Face, Zenodo and DataCite. |
| **Background downloads** | Datasets download while you keep working, are verified on arrival and stored once for reuse across projects. |
| **Project notebooks** | Each project has its own notebook and Python kernel, isolated in a Docker container. The notebook panel offers Jupyter-style cells with syntax highlighting. |
| **Safe defaults** | Risky code and deletions require confirmation, and only public HTTPS hosts are contacted. |
| **Model choice** | Works with any chat model available on OpenRouter. |

## Getting started

### Prerequisites

- **Python** 3.11, 3.12 or 3.13
- An **[OpenRouter API key](https://openrouter.ai/keys)**
- **Docker** (optional, required only to run notebook code)

### Installation

```bash
git clone https://github.com/uV-naloN/SillyTherin.git
cd SillyTherin
scripts/setup.sh    # creates .venv, installs dependencies and creates .env
```

<details>
<summary>Windows</summary>

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

</details>

Add your API key to `.env`:

```dotenv
OPENROUTER_API_KEY=sk-or-...
```

To run notebook code, build the kernel image with Docker running:

```bash
docker build -f docker/kernel.Dockerfile -t sillytherin-kernel:1 docker
```

### Running

```bash
.venv/bin/python -m sillytherin
```

Open <http://127.0.0.1:8000>. The app is being moved to its own web page: until that page is built, the server runs but shows no chat page (use the `main` branch for the current app).

## Configuration

| File | Purpose |
|---|---|
| `.env` | Secrets: `OPENROUTER_API_KEY`, and optionally `OPENROUTER_MODEL` to override the default model |
| `config.toml` | Default model, system prompt, search and download limits, thinking levels |

Restart the app after changing any of these files. Projects, notebooks and datasets are stored in `workspaces/`, which is excluded from version control.

### OpenRouter relay errors

If the chat reports that OpenRouter couldn’t route the request, check
[OpenRouter status](https://status.openrouter.ai/) and retry through the existing
composer once the service recovers. If a tool already ran, review its result
before submitting the same action again.

For troubleshooting, compare the HTTP status from an authenticated
`GET /api/v1/key` with the status and error from an authenticated
`POST /api/v1/chat/completions` with an empty JSON object. A key check of 200
means that endpoint accepted the key. A matching relay 400 on the chat
endpoint reproduces the symptom without a chat prompt. If the error persists,
share the timestamp with timezone, method, path, status and fixed error
message with [OpenRouter support](https://openrouter.ai/support/). Keep keys,
authorization headers and conversation contents out of the report.

The server logs
`OpenRouter chat request failed reason=relay_failed http_status=400`.

### Optional Langfuse reply tracing

Tracing is off by default. Use a Langfuse Cloud development project for local testing.
Trace data can include submitted messages, model inputs and replies, and private notebook
or document text. Configure project access and retention before enabling it, and keep
the project keys in your local `.env`.

Install the tracing dependency into the same virtual environment that runs SillyTherin.

```bash
.venv/bin/python -m pip install -r requirements-langfuse.txt
```

Add these settings to `.env` and replace the placeholders with keys from the same
Langfuse Cloud project. Set the region to `eu`, `us`, or `jp` to match that project.

```dotenv
SILLYTHERIN_LANGFUSE_ENABLED=true
SILLYTHERIN_LANGFUSE_REGION=eu
LANGFUSE_PUBLIC_KEY=your-project-public-key
LANGFUSE_SECRET_KEY=your-project-secret-key
```

Restart SillyTherin with `.venv/bin/python -m sillytherin`. For a single traced run on
Linux or macOS, keep the region and keys in `.env`, omit the enable flag from that
file, and use this command to start the server.

```bash
SILLYTHERIN_LANGFUSE_ENABLED=true .venv/bin/python -m sillytherin
```

To verify tracing, send `Hello` in SillyTherin and find the new `reply.turn` trace
in your Langfuse project. Check that its input and output match the prompt and
reply. If the trace is missing, check the SillyTherin terminal for export warnings.
To disable tracing, remove the enable flag from `.env` and restart, or restart
without the environment prefix if you enabled tracing for one run.

Traces contain the submitted message, handed-off reply, timing and outcome.
Stopped turns have a cancelled outcome and error status, with partial or missing
reply text. Each text field is limited to 64 KiB UTF-8. Oversized traces are
dropped with a content-free diagnostic. Existing traces keep their original data.
Approval waiting counts toward the reply total and is excluded from tool duration. If export fails or the queue fills, the reply
still completes and the server log records a reason and affected trace count.

Use synthetic replies and tools to check a live test project. For the separate
SDK test suite, run `.venv/bin/python -m pytest integration/langfuse` after
installing the optional requirements. Those tests do not send network requests.

## Security

SillyTherin executes code, so it only serves `localhost` and refuses to start on any other host. It also refuses requests from any other website open in your browser. Do not expose it on a network (for example with `--host 0.0.0.0`) unless you first add authentication.

## Development

To inspect local model calls and replies, see [Optional Langfuse reply tracing](#optional-langfuse-reply-tracing).

Install the development dependencies, then run the tests and the linter:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -m "not docker"    # tests that do not need Docker
.venv/bin/python -m ruff check .
```

The page (chat and notebook panel) is a React + TypeScript app in `frontend`:

```bash
cd frontend
npm ci
npm test
npm run build    # writes the page to frontend/dist, which is committed
```

For live reload, start the app with `python -m sillytherin --dev`, run `npm run dev` in `frontend`, and open <http://localhost:5173>.

Contributions are welcome. Please open an issue to discuss larger changes before submitting a pull request.

## License

Released under the [Apache License 2.0](LICENSE).

Built with [FastAPI](https://fastapi.tiangolo.com), [OpenRouter](https://openrouter.ai) and [CodeMirror](https://codemirror.net).
