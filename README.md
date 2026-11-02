# Company Verification & Enrichment Service

An auditable, multi-stage verification pipeline engineered to achieve **near-100% accuracy** for company firmographics and legal identity resolution.

Unlike conventional commercial enrichment APIs that rely on stale aggregations and probabilistic fuzzy joins, this service treats enrichment as an **auditable consensus pipeline**:
1. **Resolves canonical identity** across TLD migrations, subdomains, and 301/302 redirects.
2. **Harvests multi-tier evidence in parallel** from first-party company websites, schema.org JSON-LD, and official government registries (US SEC EDGAR, UK Companies House).
3. **Reconciles competing claims** using a deterministic Source Authority Matrix (`Government Registry > First-Party Terms > Commercial`).
4. **Enforces character-span grounding audits** against raw scraped text to strictly reject LLM hallucinations.
5. **Returns a cryptographic audit trail and provenance envelope** recording every source, timestamp, and citation quote.

---

## Architecture Overview

```
                 [User Input: Name, Website, or Both]
                                   │
                                   ▼
          ┌──────────────────────────────────────────────────┐
          │  Stage 1: Identity Resolution & Canonicalization │
          │  • Domain de-duplication & redirect tracing      │
          │  • Search API fallback (Name -> Domain)          │
          │  • Entity disambiguation & CAGE/SEC mapping      │
          └────────────────────────┬─────────────────────────┘
                                   │ Canonical Root Domain
                                   ▼
          ┌──────────────────────────────────────────────────┐
          │     Stage 2: Parallel Multi-Tier Data Fetching   │
          ├────────────────┬─────────────────┬───────────────┤
          │ Tier A: Primary│ Tier B: Official│ Tier C: B2B   │
          │ Scraping Engine│ Registries      │ API Waterfall │
          │ (Site/About/TOS│ (SEC EDGAR,     │ (PDL, Apollo, │
          │ & JSON-LD)     │ Companies House)│ Crunchbase)   │
          └────────┬───────┴────────┬────────┴───────┬───────┘
                   │                │                │
                   └────────────────┼────────────────┘
                                    │ Raw Data Streams
                                    ▼
          ┌──────────────────────────────────────────────────┐
          │   Stage 3: Reconciliation & Consensus Engine     │
          │  • Multi-source cross-verification               │
          │  • Deterministic Authority Matrix weighting      │
          │  • Discrepancy detection & tie-breaker logic     │
          │  • Field-level & composite confidence scoring    │
          └────────────────────────┬─────────────────────────┘
                                    │ Reconciled Fields + Score
                                    ▼
          ┌──────────────────────────────────────────────────┐
          │   Stage 4: Structured LLM Normalization & QA     │
          │  • Strict Pydantic schema validation             │
          │  • Verbatim character-span grounding audit       │
          │  • Hallucination detection & confidence penalty  │
          └────────────────────────┬─────────────────────────┘
                                   │
                                   ▼
                 [Validated JSON + Confidence Metadata]
```

---

## Zero-Credential Quickstart

The service is designed to run **out-of-the-box with zero paid API keys**:
- Uses **SEC EDGAR's public API** for US corporate filings (zero keys).
- Uses **DuckDuckGo search fallback** for Name $\rightarrow$ Domain discovery (zero keys).
- Uses **Direct First-Party Web Crawling & Schema.org JSON-LD parsing** (zero keys).
- Provides **pluggable adapters** for commercial providers (OpenCorporates, Apollo, PDL) if you choose to add keys later in `.env`.

### 1. Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Run the Development Server
```bash
uvicorn app.main:app --reload --port 8000
```
API Documentation is available at `http://localhost:8000/docs`.

### 3. Run with Docker Compose (with Redis)
```bash
docker compose up --build
```

---

## API Endpoints

### 1. Single Company Enrichment
`POST /api/v1/enrich`

#### Request Body
```json
{
  "website": "https://stripe.com",
  "force_refresh": false
}
```
Or by company name with optional jurisdiction hint:
```json
{
  "name": "Stripe",
  "jurisdiction_hint": "US"
}
```

#### Response Envelope (HTTP 200)
```json
{
  "status": "success",
  "request_id": "94cdb43e-a63d-46b3-ba0f-20f958ce9f4d",
  "canonical_domain": "stripe.com",
  "confidence_score": 0.985,
  "confidence_tier": "HIGH_ASSURANCE",
  "data": {
    "legal_name": "Stripe, Inc.",
    "brand_name": "Stripe",
    "status": "Active",
    "founded_year": 2010,
    "headquarters": {
      "street": "354 Oyster Point Blvd",
      "city": "South San Francisco",
      "state": "CA",
      "postal_code": "94080",
      "country": "USA",
      "formatted_address": "354 Oyster Point Blvd, South San Francisco, CA 94080"
    },
    "employee_range": "5001-10000",
    "industry": {
      "sector": "Financial Services",
      "sic_code": "7372",
      "label": "Financial Transactions Processing"
    },
    "website": "https://stripe.com",
    "active_web_presence": true
  },
  "provenance": {
    "legal_name": {
      "source": "US Securities and Exchange Commission (SEC EDGAR)",
      "source_type": "GOVERNMENT_REGISTRY",
      "confidence": 1.0,
      "filing_id": "0001628280",
      "verified_at": "2026-11-01T23:50:00Z"
    },
    "headquarters": {
      "source": "https://stripe.com/privacy",
      "source_type": "FIRST_PARTY_TERMS",
      "confidence": 0.96,
      "exact_quote": "354 Oyster Point Blvd, South San Francisco, CA 94080",
      "verified_at": "2026-11-01T23:50:00Z"
    }
  },
  "discrepancies": [],
  "candidates": [],
  "metadata": {
    "latency_ms": 1150.4,
    "cache_hit": false,
    "freshness_timestamp": "2026-11-01T23:50:00Z",
    "pages_crawled": 5,
    "evidence_count": 14
  }
}
```

### 2. Batch Enrichment
`POST /api/v1/enrich/batch`
Enriches up to 50 companies concurrently with bounded execution deadlines.

### 3. Historical Provenance Audit
`GET /api/v1/enrich/{domain}/audit`
Fetches the cached verification envelope and audit log for a given domain.

### 4. Health Check
`GET /health`

---

## Running the Verification Test Suite

Run unit and integration tests:
```bash
pytest -v
```

All 17 automated tests cover:
- TLD redirect chasing and subdomain normalization.
- Schema.org JSON-LD structured data extraction.
- First-party crawler for `/about`, `/terms`, `/privacy`, `/contact`.
- Source Authority Matrix and dispute resolution logic.
- Character-span verbatim grounding audits.
- Full mocked and live FastAPI endpoints.

