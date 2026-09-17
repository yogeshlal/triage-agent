# LAVA Triage Agent

Standalone HTTP service that triages LAVA job failures. Give it a job ID, it
fetches the job log, runs the `skills/kernel-triage` skill via a configurable LLM,
and produces a Markdown root-cause report.

- Log fetch: existing `lava_helper_script/lava_client.py`.
- Triage logic: existing `skills/kernel-triage/` (SKILL.md + references + CoTs).
- Model: any LiteLLM-supported provider (Claude / GPT / Gemini / Ollama / …).
- Concurrency: async — POST returns a `run_id`; caller polls GET.

## Setup

```
python3.10 -m venv .venv
source .venv/bin/activate.csh       # or activate for bash/zsh
pip install -r requirements.txt
cp .env.example .env
$EDITOR .env                        # fill LAVA_URL, LAVA_TOKEN, OPENAI_API_KEY
```

On startup the service loads `.env` into the process environment
(`python-dotenv`) so LiteLLM and the LAVA client both see the same
credentials. Variables already exported in the shell win over `.env`
(nothing is silently overridden).

Start the service:

```
python -m agent_service.main        # uvicorn on :8080
```

## Model provider

Any LiteLLM-supported provider works. Pick one and set the matching env
vars — see `.env.example` for the menu.

- **Anthropic direct** — `TRIAGE_MODEL_DEFAULT=anthropic/claude-4-5-sonnet`
  and `ANTHROPIC_API_KEY=…`
- **OpenAI** — `TRIAGE_MODEL_DEFAULT=openai/gpt-4o` and `OPENAI_API_KEY=…`
- **OpenAI-compatible proxy** (Azure OpenAI, an internal gateway, etc.) —
  keep the `openai/*` provider prefix and add `OPENAI_API_BASE=<proxy URL>`
- **Google Gemini** — `TRIAGE_MODEL_DEFAULT=gemini/gemini-1.5-pro` and
  `GOOGLE_API_KEY=…`
- **Local Ollama** — `TRIAGE_MODEL_DEFAULT=ollama/llama3.1:70b` and
  `OLLAMA_API_BASE=http://ollama:11434`

Per-request overrides via the `model` and `model_options` fields on
`POST /{job_id}` — see below.

## Endpoints

### `POST /{job_id}`

Submit a triage run. The LAVA job id is in the URL path. The body is
optional:

```
POST http://localhost:8080/1234567
Content-Type: application/json

{
  "node_id": "6a980789e41d7f97d6f1dcec",
  "model": "anthropic/claude-4-5-sonnet",
  "model_options": {"temperature": 0.1, "max_tokens": 8192}
}
```

`node_id` is the KernelCI (Maestro) node for the job. When supplied, the
service fetches that node through `kci-dev`'s `KernelCIClient` and gives the
agent the KernelCI-side picture — tree, branch, commit, platform, lab — so
the RCA is not based on the LAVA log alone. The full node JSON is written
into the run workspace as `kernelci_node_{node_id}.json` for the agent to
read on demand. Node reads are unauthenticated; set `TRIAGE_KCI_API_URL` if
you are not pointing at production. A lookup failure is non-fatal — triage
proceeds on the log.

`model` and `model_options` are optional; when omitted the service uses
`TRIAGE_MODEL_DEFAULT`. `model_options` is merged verbatim into the LiteLLM
`completion()` call — pass anything LiteLLM accepts (`api_base`,
`temperature`, `max_tokens`, provider-specific args).

A fourth field, `log_url`, overrides where the log comes from. Log
resolution is: `log_url`, then the node's `artifacts.lava_log`, then the
LAVA REST API. Callers that pass `node_id` do not need it.

Reserved names (`health`, `triage`, `docs`, `openapi.json`, `redoc`) are
rejected as job ids with HTTP 400.

If the service already has `TRIAGE_MAX_ACTIVE_RUNS` in-flight (default 16),
new submissions are rejected with HTTP 503 and a message like
`too many active runs (16/16); retry later`.

Response (202):

```json
{"run_id": "a1b2c3d4e5f6", "status": "queued"}
```

### `GET /triage/{run_id}`

Response:

```json
{
  "run_id": "a1b2c3d4e5f6",
  "job_id": "1234567",
  "node_id": "6a980789e41d7f97d6f1dcec",
  "model": "anthropic/claude-4-5-sonnet",
  "status": "completed",
  "started_at": "...",
  "finished_at": "...",
  "iterations": 18,
  "report_path": "./reports/a1b2c3d4e5f6_report.md",
  "report": "# Crash Analysis\n\n...",
  "error": null
}
```

`status` progresses `queued` → `running` → `completed | failed`. On `failed`,
`error` is a short human-readable message (see `agent_service/runner.py` for
the full mapping). 404 is returned for unknown `run_id`.

## Report delivery

Polling `GET /triage/{run_id}` is always available, but the service can also
push the finished report out. Both channels are optional and gated
independently — see `.env.example` for the full list.

- **GitHub issue** — `TRIAGE_GITHUB_REPO` + `TRIAGE_GITHUB_TOKEN`. The first
  failure for a `node_id` opens an issue; later runs for the same `node_id`
  comment on it instead. De-duplication works by searching `in:body
  <node_id>`, which is why the report body always carries the node id.
- **Email** — `TRIAGE_SMTP_HOST` + `TRIAGE_SMTP_FROM` + `TRIAGE_EMAIL_TO`.
  STARTTLS on port 587, implicit TLS on 465. Sends a plain-text body with an
  HTML alternative when `markdown` is installed.

Delivery runs on both terminal outcomes: a `failed` run delivers an
"Analysis unavailable" note with the error, so a broken triage is visible
rather than silent. Delivery can never change a run's status.


### `GET /health`

Liveness + config sanity check. Returns default model, skill dir, and count
of in-flight runs.

## Smoke test (tcsh)

```
curl -sX POST http://localhost:8080/1234567 | tee /tmp/q.json

set RID=`jq -r .run_id /tmp/q.json`
while true
    set S=`curl -s http://localhost:8080/triage/$RID | jq -r .status`
    echo $S
    if ( "$S" == "completed" || "$S" == "failed" ) break
    sleep 15
end
cat reports/${RID}_report.md
```

The RCA report includes a clickable `**LAVA Job URL:**` in the
Executive Summary (constructed from `LAVA_URL` + the job id), so
reviewers can jump straight to the LAVA job page.

Swap in a different model per request (e.g. local Ollama):

```
curl -sX POST http://localhost:8080/1234567 \
    -H 'content-type: application/json' \
    -d '{"model":"ollama/llama3.1:70b","model_options":{"api_base":"http://ollama:11434","temperature":0.1}}'
```

## Architecture

See `agent_service/`:

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app + uvicorn entrypoint |
| `api.py` | Route handlers |
| `config.py` | Environment-driven settings |
| `schemas.py` | Request / response models |
| `store.py` | Thread-safe in-memory run store |
| `runner.py` | Worker: LAVA fetch → agent → status update |
| `agent.py` | LiteLLM tool-calling loop |
| `tools.py` | Sandboxed file tools (`read_file`, `list_dir`, `grep`, `write_report`) |
| `prompts.py` | System prompt builder |

The agent gets file-read tools only; nothing outside the skill directory and
the per-run workspace is reachable. `write_report` writes to a fixed path set
by the runner, so tool arguments cannot influence the report location.

## Corporate TLS proxy

On networks that intercept outbound TLS with a self-signed corporate CA, the
call to Anthropic (or OpenAI/Gemini) fails with:

```
SSL: CERTIFICATE_VERIFY_FAILED — self-signed certificate in certificate chain
```

Two ways to fix it:

1. **Trust the corp CA properly** — set `TRIAGE_LLM_CA_BUNDLE` to the PEM
   file for your organisation's CA. This wires `SSL_CERT_FILE` and
   `REQUESTS_CA_BUNDLE` at startup so both `httpx` (LiteLLM / Anthropic SDK)
   and `requests` (LAVA client) trust the intercepted chain.

    ```
    setenv TRIAGE_LLM_CA_BUNDLE /etc/ssl/certs/ca-certificates.crt
    ```

2. **Disable verification** (last resort — logs a WARNING at startup):

    ```
    setenv TRIAGE_LLM_SSL_VERIFY false
    ```
