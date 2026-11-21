# Greg — Autonomous CFO (Red Hat OpenShift AI & Commerzbank Collabothon 2026)

**Greg** is a plain-language **Autonomous CFO** assistant for small and medium-sized businesses (*"Innovations for all generations"*). It starts empty per browser tab, accepts arbitrary bank CSVs with interactive column mapping, automatically selects between a **12-month seasonal hybrid ML/statistical planner** (`>= 12` full months) and a **daily statistical liquidity fallback** (`< 12` months), and pairs deterministic figures with an OpenAI-compatible Red Hat OpenShift AI LLM endpoint.

> **Core Principle:** *Import Statement -> Analyze Automatically -> Inspect Monthly/Daily Cash Outlook -> Explain in Everyday Language.*

---

## Project Architecture

```text
src/greg/
├── __init__.py                 # Public entrypoint facade (exports `main`)
├── __main__.py                 # `python -m greg` launcher
├── config.py                   # Dynamic env settings loader (`load_settings()`)
├── api/
│   ├── __init__.py
│   ├── routes.py               # REST routes, session headers, hardened `/static/` server
│   ├── serializers.py          # JSON request/response parsing & encoding helpers
│   └── server.py               # ThreadingHTTPServer factory & CLI `--check` entrypoint
├── domain/
│   ├── __init__.py
│   ├── categories.py           # Deterministic keyword classifier + social-security guardrail
│   ├── csv_import.py           # CSV delimiter/date/amount parsing, column map, FX conversion
│   ├── ledger.py               # In-memory `LedgerStore` & monthly/daily view aggregation
│   └── sessions.py             # Thread-safe per-tab `SessionManager` (TTL + LRU cap)
├── forecasting/
│   ├── __init__.py
│   ├── models/                 # `planner.keras`, `planner_config.json`, model notes
│   ├── lstm_numpy.py           # Pure-NumPy Keras 3 `.keras` Seq2Seq-BiLSTM inference engine
│   ├── seasonal.py             # `SeasonalPlanner`: classic + BiLSTM + StatsForecast backtester
│   ├── daily.py                # Daily recurring-bill + weekday-average fallback forecaster
│   └── orchestrator.py         # Snapshot-cached forecast router (`>= 12m` vs `< 12m`)
├── llm/
│   ├── __init__.py
│   ├── client.py               # OpenAI-compatible HTTP client (`call_llm`, `/models` discovery)
│   ├── classifier.py           # Optional LLM signature classifier (`Salaries`/`Taxes`/`Other`)
│   ├── guardrails.py           # Optional NeMo Guardrails input screening (`/v1/guardrail/checks`)
│   └── prompts.py              # Grounded system & explain prompt builders
└── static/
    ├── index.html              # Semantic UI shell
    ├── fonts/                  # Variable `Inter` webfont & license
    ├── css/
    │   ├── base.css            # Layout, typography, forms, number steppers, custom selects
    │   ├── forecast.css        # Dark forecast stage, interactive SVG chart, method bar, carousel
    │   └── messenger.css       # Floating Greg chat launcher & messenger dialog
    └── js/
        ├── app.js              # State coordination & event bindings
        ├── api.js              # Per-tab `X-Greg-Session` client
        ├── chart.js            # Interactive zoom/pan/crosshair SVG chart
        ├── chat.js             # Floating messenger & `/api/explain` controller
        ├── controls.js         # Carousel navigation, number steppers, dot-grid hover
        ├── csv-mapping.js      # Header detection, currency inference, column mapping UI
        └── format.js           # Currency & HTML escaping helpers

tests/
├── helpers.py                  # Shared synthetic ledger builders & HTTP mock helpers
├── unit/                       # Domain, NumPy BiLSTM, seasonal planner, LLM, session tests
└── integration/                # HTTP workflow, routing, traversal hardening, static asset tests
```

---

## Key Capabilities

1. **Flexible CSV Import & Column Mapping**
   - Supports `;`, `,`, and tab delimiters, EU/US number formats, ISO/`DD.MM.YY`/`YYYY-MM` dates, single signed `Amount` columns, or split Income/Expenses columns (e.g. `date,revenue_pln,expenses_pln`), automatic currency suffix inference, and inline `@ <rate> EUR/<CURR>` FX conversion.
   - Categorizes transactions into strictly **`Salaries`**, **`Taxes`**, or **`Other`**, keeping social-security and health-insurance funds out of `Taxes`.

2. **Two-Tier Adaptive Forecasting**
   - **Seasonal Monthly Planner (`>= 12` full months)**: Evaluates **Classic (Seller Method)**, **Own Model (Seq2Seq BiLSTM via pure NumPy)**, and **StatsForecast** (`AutoETS`, `AutoARIMA`, `AutoTheta`, and `Combo`) via walk-forward backtesting on up to the last 3 years, displaying the chosen method and selection notes in the UI alongside a pessimistic stress-test trajectory.
   - **Daily Fallback (`< 12` full months)**: Detects monthly/quarterly recurring bills, computes weekday non-recurring averages, overlays future manual operations, and projects 30/60/90-day daily balances.

3. **Tab-Isolated Stateful Sessions & Zero-Leakage Headers**
   - Every browser tab maintains an isolated in-memory ledger via `X-Greg-Session` backed by TTL/LRU eviction, and HTML/JSON/JS/CSS responses enforce `Cache-Control: no-store`.

---

## Local Development & Verification

```bash
# Install runtime + dev dependencies (pandas, numpy, h5py, statsforecast, ruff)
uv sync

# Lint source & tests with Ruff
uv run ruff check src tests

# CLI health smoke check
uv run greg --check

# Run unit & integration tests
uv run python -m unittest discover -s tests -v

# Start local server on http://127.0.0.1:8000
uv run greg
```

### Environment Variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | HTTP bind host |
| `PORT` | `8000` | HTTP bind port |
| `LLM_BASE_URL` | `""` | OpenAI-compatible `/v1` base URL (e.g. OpenShift AI vLLM service) |
| `LLM_MODEL` | `""` | Model ID (auto-discovered via `/models` when omitted) |
| `LLM_API_KEY` | `""` | Optional bearer token for the LLM endpoint |
| `GREG_PLANNER_DIR` | package `forecasting/models` | Override directory containing `planner.keras` & `planner_config.json` |
| `GREG_SESSION_MAX` | `50` | Maximum concurrent tab sessions cached in memory |
| `GREG_SESSION_TTL` | `7200` | Session idle expiry in seconds |
| `GUARDRAILS_CHECK_URL` | `""` | Full NeMo Guardrails checks URL (`.../v1/guardrail/checks`); unset disables screening |
| `GUARDRAILS_TOKEN` | `""` | Bearer token for the guardrails endpoint (defaults to the pod's service account token) |
| `GUARDRAILS_FAIL_OPEN` | `false` | When `true`, chat still works if the guardrails service is unreachable |

---

## Container & OpenShift Deployment

Build and deployment manifests live in [`deploy/`](deploy/), built cleanly with [`Containerfile`](Containerfile) (`uv sync --frozen --no-dev --extra planner`) and automated on `main` via [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml).

---

## LLM Security

**Always on (in code):**
- `/api/chat` rejects client-supplied `system` messages, always injects Greg's own grounded system prompt, accepts only `user`/`assistant` turns of at most 4,000 characters, and keeps the last 12.
- The system prompt limits Greg to the business's finances and tells the model to treat CSV names and references as data. CSV-derived names are flattened to one short line before they are embedded.
- The CSV classifier sends transactions as JSON lines inside a `<transactions>` block marked as untrusted, and only accepts `Salaries`/`Taxes`/`Other` back.

**NeMo Guardrails (optional, OpenShift AI 3.5+):** every chat message is screened by a DeBERTa prompt-injection classifier before it reaches the model. Blocked messages return `422` with a polite refusal; if the guardrails service is down, chat returns `503` unless `GUARDRAILS_FAIL_OPEN=true`.

```bash
oc apply -n my-first-model -f deploy/security/   # guardrails server, RBAC, egress NetworkPolicy
oc get nemoguardrails,svc -n my-first-model      # find the guardrails service name and port
```

Then set `GUARDRAILS_CHECK_URL` in [`deploy/configmap.yaml`](deploy/configmap.yaml) to `http(s)://<service>.my-first-model.svc.cluster.local:<port>/v1/guardrail/checks` and redeploy. If the service serves HTTPS with an OpenShift service-CA certificate, also mount the service CA bundle and point `SSL_CERT_FILE` at it.

`deploy/security/` is not applied by CI, because the `NemoGuardrails` CRD only exists once the TrustyAI component is enabled.

