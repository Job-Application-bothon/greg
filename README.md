# Greg — Autonomous CFO for Pizzeria Amalfi (Red Hat OpenShift AI)

**Greg** is a calm, plain-language **Autonomous CFO** assistant built for small business owners (*"Innovations for all generations"*). It pairs a **deterministic `Decimal` financial engine** with a **single Red Hat OpenShift AI LLM endpoint** (`vLLM` / `llama-32-3b-instruct`), pre-loaded with the **Pizzeria Amalfi 2025 Bank Statement (1,125 bookings)** and supporting custom bank CSV uploads.

> **Core Principle:** *Analyze → Explain Clearly → Simulate What-Ifs → Prepare Bank Actions (Nothing Happens Without Owner Approval).*

---

## Architecture Overview

1. **Deterministic Financial Engine (`src/greg/engine.py`)**
   - Parses German MT940-style semicolon bank CSVs (`DD.MM.YY`, comma decimals, `@ rate EUR/CHF`) and standard comma CSVs with exact `Decimal` math.
   - Computes historical daily balances (identifying Pizzeria Amalfi's **July 3, 2025 lowest balance of `-€22,097.60`** during the 3-week June shutdown and **`+€33,266.35` year-end closing balance**), a **13-Week Cash Forecast**, interactive **What-If Simulations**, **Commerzbank Action Cards** (Operating Credit Line, EUR/CHF Rate Lock, 60-Day *Festgeld*, Automatic Tax Pot, and Suspicious Invoice Check), and **German Tax & Grant Tips**.
2. **Single OpenShift AI LLM Integration (`src/greg/__init__.py`)**
   - Connects over internal OpenShift Service DNS (`LLM_BASE_URL` & `LLM_MODEL` from [`deploy/configmap.yaml`](deploy/configmap.yaml)) to a single OpenAI-compatible `/v1/chat/completions` predictor.
   - Falls back gracefully to deterministic plain-language summaries when running offline outside the OpenShift cluster.
3. **Intuitive Web Interface (`src/greg/static/index.html`)**
   - Designed for business owners of all technical skill levels: clear labels matching chart callouts (`Lowest Balance`, `Current Bank Balance`, `Lowest Expected Balance`), interactive SVG cash charts, plain-English explanations, formatted Markdown AI answers, and one-click human approval receipts.

---

## Privacy, PII & IBAN Anonymization Before OpenShift AI Inference

Before any financial context or chat question leaves the `greg` pod toward the OpenShift AI LLM endpoint, `anonymize_text()` and `build_anonymized_context_prompt()` in [`src/greg/engine.py`](src/greg/engine.py) automatically mask IBANs and personal/counterparty names into deterministic reference tokens—and restore them only on the authorized response inside the pod.

### Tokenization Mapping

| Sensitive Field | Original Value | Token Sent to OpenShift AI LLM |
| :--- | :--- | :--- |
| Business IBAN | `DE89370400440532013000` | `IBAN_TOK_3000` |
| Partner IBAN | `CH9300700110001234567` | `IBAN_TOK_4567` |
| Owner Name | `Vincenzo Amalfi` | `OWNER_REF_001` |
| Business Name | `Pizzeria Amalfi` | `SME_ENTITY_001` |
| Partner / Beneficiary | `Luigi Amalfi` / `Luigi's Ristorante Zurich` | `PARTNER_REF_002` / `PARTNER_BIZ_002` |
| Suppliers | `Asimex Wholesale Ltd`, `Fattoria La Vino GmbH` | `VENDOR_REF_001`, `VENDOR_REF_002` |
| Staff Names | `Yusuf Akcit`, `Rosa Pavese`, `Vanessa Paradi` | `STAFF_REF_001`..`STAFF_REF_003` |

### Example Anonymized System Prompt Sent to OpenShift AI

```text
You are Greg, a clear, friendly Autonomous CFO assistant powered by Red Hat OpenShift AI for a small business owner.
Use plain, everyday language without accounting jargon. All Euro amounts below are verified by the deterministic engine — never invent numbers.

ACTIVE DATA: Pizzeria Amalfi 2025 Bank Statement (1,125 Transactions)
BUSINESS PROFILE: SME_ENTITY_001 (Sole Proprietorship (Einzelunternehmen)), Location: Frankfurt am Main, Hessen, DE, Industry: Gastronomy & Hospitality (NACE 56.10), Staff: 4, IBAN: IBAN_TOK_3000
CONTEXT NOTES: 4 staff payroll (€8,600/mo on 28th) + Techniker Krankenkasse (€2,650/mo on 27th) + Rent (€3,800/mo on 1st). 3-week June vacation closure pushed 2025 balance to -€22,097.60 before Q3/Q4 recovery to +€33,266.35. Monthly -4,166.67 CHF silent partnership transfer to PARTNER_BIZ_002 (50,000 CHF total).
SAFETY RESERVE TARGET: €12,000.00

VERIFIED FINANCIAL NUMBERS:
- Current Bank Balance: €33,266.35
- Lowest Balance in History: €-22,097.60 on 2025-07-03
- Lowest Expected Balance (Next 13 Weeks): €-8,420.00 (Week 7)
- Lowest Balance With Commerzbank Actions: €14,500.00 (Week 6)
- Foreign Currency Transfers: 4,166.67 CHF / month to Zurich (€26,184.45)
- Business Health Score: 66/100 (Watch) | Clean Data Score: 99.2%
```

All approvals also generate a simulated Commerzbank confirmation receipt (`CBK-EXEC-...`) backed by a chained SHA-256 audit log (`create_audit_entry()`).

---

## Local Development & Verification

```bash
# Run CLI health smoke check
uv run greg --check

# Run unit & HTTP API integration tests
uv run python -m unittest discover -s tests -v

# Start local server on http://127.0.0.1:8000
uv run greg
```

### OpenShift Deployment

Manifests are in [`deploy/`](deploy/) and built automatically via [`Containerfile`](Containerfile) and [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml).
Set `LLM_BASE_URL`, `LLM_MODEL`, and optional `LLM_API_KEY` via [`deploy/configmap.yaml`](deploy/configmap.yaml) / `greg-llm-secret`.
