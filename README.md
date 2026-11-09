# Verifirm — Auditable Corporate Identity Resolution & Enrichment Engine

An open-source, evidence-backed corporate identity resolution and firmographics verification service.

Rather than promising universal 100% matching across every jurisdiction or silently guessing `{brand}.com` domains, **Verifirm** treats company verification as a cautious, probabilistic linkage and consensus problem with an explicit **three-way decision (`AUTO_MATCH`, `NEEDS_REVIEW`, `UNRESOLVED`)** and field-level provenance.

---

## Core Architectural Principles

1. **Three-Way Probabilistic Entity Resolution (Fellegi–Sunter Model)**:
   - **`AUTO_MATCH`**: Evidence crosses a calibrated confidence threshold ($\ge 0.75$) with a clear separation margin ($\ge 0.12$) over competing candidates.
   - **`NEEDS_REVIEW`**: Plausible competing candidates (e.g., homonyms like *"Delta Air Lines"* vs. *"Delta Faucet"*) or moderate evidence ($0.45 \le \text{score} < 0.75$). Returns all ranked candidates with supporting and contradicting signals.
   - **`UNRESOLVED`**: Insufficient or unverified evidence ($< 0.45$). **Never fabricates `{brand}.com` fallback domains.**
2. **Separation of Legal Entity, Brand, Website Assertion, and Listed Security**:
   - **`LegalEntityRecord`**: Official registered name (exact spelling & casing preserved without `.title()` mutation), jurisdiction, registration number, LEI, CIK, statutory status (`Active`, `Inactive`, `Dissolved`, `Unknown`), registered office address, and former names.
   - **`BrandProfile`**: Commercial brand name, operating headquarters address, verified telephone, contact email, headcount range, and description.
   - **`WebsiteAssertion`**: Treats a web domain as a separate assertion linked to the legal entity by explicit evidence (`linkage_signals`, `verification_score`, `is_independently_verified`). Website liveness (HTTP 200) is never conflated with statutory `OperatingStatus.ACTIVE`.
   - **`SecurityListing`**: Ticker symbol, exchange, and CIK/FIGI.
3. **True Source-Family Independence**:
   - Distinguishes between **primary statutory registries** (`GOVERNMENT_REGISTRY`: US SEC EDGAR, GLEIF Global LEI Index, UK Companies House), **registry aggregators** (`REGISTRY_AGGREGATOR`: OpenCorporates), **first-party web declarations** (`FIRST_PARTY_WEB`: `/terms`, Schema.org JSON-LD, `/about`, `/contact`), and **commercial vendor estimates** (`COMMERCIAL_API`: People Data Labs, Apollo).
   - Multiple extractions from the same website (`/` JSON-LD and `/terms` text) belong to a single `FIRST_PARTY_WEB` source family (`independent_sources_count = 1`) and cannot trigger a cross-source consensus boost without an independent registry or external source.
4. **Pre-Score Character-Span Grounding Audit**:
   - Every first-party textual assertion must cite a verbatim substring from the scraped corpus. Ungrounded claims are penalized *before* the composite confidence score and assurance tier are calculated.
5. **Empirical Evaluation & Calibration Benchmark**:
   - Includes a reproducible benchmark suite (`python -m benchmarks.evaluate`) measuring `AUTO_MATCH` precision, recall, abstention accuracy, per-field accuracy, and **Brier calibration score**.

---

## Architecture Overview

```
      [User Input: Name, Website, Jurisdiction, CIK, LEI, Reg#, or Ticker]
                                       │
                                       ▼
          ┌────────────────────────────────────────────────────────┐
          │  Stage 1: Cautious Identity & Candidate Resolution     │
          │  • Redirect tracing & root domain canonicalization     │
          │  • Candidate generation (Wikidata P856, Brave/DDG)     │
          │  • Landing-page verification guard                     │
          │  • Fellegi-Sunter 3-Way Decision:                      │
          │    AUTO_MATCH | NEEDS_REVIEW | UNRESOLVED              │
          └────────────────────────────┬───────────────────────────┘
                                       │
                                       ▼
          ┌────────────────────────────────────────────────────────┐
          │  Stage 2: Parallel Multi-Tier Evidence Harvesting      │
          ├──────────────────┬───────────────────┬─────────────────┤
          │ Tier A: 1st-Party│ Tier B: Official  │ Tier C: Optional│
          │ Web & JSON-LD    │ Registries        │ Aggregators &   │
          │ (/, /terms,      │ (SEC EDGAR,       │ Vendors (Open-  │
          │ /about, /contact)│ GLEIF LEI, UK CH) │ Corporates, PDL)│
          └────────┬─────────┴─────────┬─────────┴────────┬────────┘
                   └───────────────────┼──────────────────┘
                                       │ RawEvidenceBag
                                       ▼
          ┌────────────────────────────────────────────────────────┐
          │  Stage 3 & 4: Grounding Audit & Calibrated Consensus   │
          │  • Verbatim character-span grounding check (pre-score) │
          │  • Authority Matrix + extractor confidence weighting   │
          │  • SourceFamily independence verification              │
          │  • Completeness-adjusted composite confidence score    │
          └────────────────────────────┬───────────────────────────┘
                                       │
                                       ▼
             [EnrichmentResponse: LegalEntity + Brand + Provenance]
```

---

## Zero-Key Quickstart

The core verification pipeline runs out-of-the-box with **zero paid API keys**:
- **US SEC EDGAR API** (zero-key; rate-limited to $\le 10$ req/s per SEC fair-access policy) for US reporting issuers.
- **GLEIF Global LEI API** (zero-key) for global legal entities, statutory addresses, and registration numbers.
- **Wikidata Knowledge Graph (`P856`) + DuckDuckGo** (zero-key) as candidate generators verified by live landing-page inspection.
- **Optional Pluggable Adapters**: Configure `BRAVE_SEARCH_API_KEY`, `COMPANIES_HOUSE_API_KEY`, `OPENCORPORATES_API_KEY`, `PDL_API_KEY`, or `APOLLO_API_KEY` in `.env` when needed.

### 1. Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Start the FastAPI Server
```bash
uvicorn app.main:app --reload --port 8000
```
Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.

### 3. Run with Docker Compose (with Redis)
```bash
docker compose up --build
```

---

## API Usage Examples

### 1. Enrich by Website, Name, or Exact Registry Identifier (`POST /api/v1/enrich`)

#### By Exact Identifier (LEI, CIK, or Ticker)
```json
{
  "name": "NVIDIA",
  "ticker": "NVDA",
  "jurisdiction_hint": "US"
}
```

#### By Company Name or Website
```json
{
  "website": "https://stripe.com",
  "force_refresh": false
}
```

#### Response Envelope (HTTP 200)
```json
{
  "status": "success",
  "match_decision": "AUTO_MATCH",
  "request_id": "94cdb43e-a63d-46b3-ba0f-20f958ce9f4d",
  "canonical_domain": "nvidia.com",
  "confidence_score": 0.902,
  "confidence_tier": "HIGH_ASSURANCE",
  "data": {
    "legal_name": "NVIDIA CORP",
    "brand_name": "NVIDIA",
    "status": "Active",
    "headquarters": {
      "street": "2788 SAN TOMAS EXPRESSWAY",
      "city": "SANTA CLARA",
      "state": "CA",
      "postal_code": "95051",
      "country": "USA",
      "formatted_address": "2788 SAN TOMAS EXPRESSWAY, SANTA CLARA, CA, 95051"
    },
    "registered_office": {
      "street": "251 LITTLE FALLS DRIVE",
      "city": "WILMINGTON",
      "state": "DE",
      "postal_code": "19808",
      "country": "USA",
      "formatted_address": "251 LITTLE FALLS DRIVE, WILMINGTON, DE, 19808"
    },
    "cik": "0001045810",
    "jurisdiction": "US-DE",
    "active_web_presence": true,
    "legal_entity": {
      "registered_name": "NVIDIA CORP",
      "jurisdiction": "US-DE",
      "cik": "0001045810",
      "legal_status": "Active"
    },
    "website_assertion": {
      "canonical_domain": "nvidia.com",
      "website_url": "https://nvidia.com",
      "is_online": true,
      "linkage_signals": [
        "official_registry_legal_entity_verified",
        "wikidata_p856_candidate",
        "landing_page_title_match",
        "http_live_response"
      ],
      "verification_score": 0.8,
      "is_independently_verified": true
    },
    "securities": [
      {
        "ticker": "NVDA",
        "exchange": "Nasdaq",
        "cik": "0001045810"
      }
    ]
  },
  "provenance": {
    "legal_name": {
      "source": "US Securities and Exchange Commission (SEC EDGAR)",
      "source_type": "GOVERNMENT_REGISTRY",
      "confidence": 0.96,
      "exact_quote": "SEC CIK 0001045810: NVIDIA CORP",
      "filing_id": "0001045810",
      "independent_sources_count": 2
    }
  },
  "discrepancies": [],
  "candidates": []
}
```

### 2. Autonomous Verification Agent (`POST /api/v1/agent/enrich`)
Dispatches `CompanyEnrichmentAgent` using an auditable ReAct reasoning loop and returns both the `EnrichmentResponse` and the step-by-step `AgentTrajectory`.

---

## Testing & Empirical Benchmark Evaluation

### Run Unit & Integration Tests (32 Tests)
```bash
.venv/bin/pytest -v
```

### Run Golden Benchmark & Calibration Suite
Evaluates identity resolution precision, recall, abstention on homonyms/unresolvable inputs, field-level accuracy, and Brier calibration score across [`benchmarks/golden_companies.json`](benchmarks/golden_companies.json):
```bash
.venv/bin/python -m benchmarks.evaluate
```
