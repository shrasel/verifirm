import asyncio
import json
from pathlib import Path
from typing import Any
import httpx

from app.models.request import EnrichmentRequest
from app.models.response import MatchDecision
from app.pipeline.orchestrator import EnrichmentPipelineOrchestrator


def create_benchmark_mock_transport() -> httpx.MockTransport:
    """
    Deterministic mock transport simulating live responses for the 8 golden benchmark cases:
    - US public companies (NVIDIA, Meta Platforms)
    - Private company with Schema.org JSON-LD & /terms (Stripe)
    - Non-US company via GLEIF LEI without website (Siemens AG)
    - Inactive / deregistered SEC filer (Lehman Brothers Holdings Inc)
    - Ambiguous homonym ('Delta' -> Delta Air Lines vs Delta Faucet)
    - Unresolvable nonexistent entity ('Xyzzy Nonexistent Global Holdings LLC')
    - Parked domain trap ('ParkedVentureTrap Corp')
    """
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # 1. Wikidata API
        if "wikidata.org/w/api.php" in url:
            if "wbsearchentities" in url:
                search_param = request.url.params.get("search", "").lower()
                if "nvidia" in search_param:
                    return httpx.Response(200, json={
                        "search": [{"id": "Q182477", "label": "Nvidia", "description": "American technology company"}]
                    })
                if "meta platforms" in search_param:
                    return httpx.Response(200, json={
                        "search": [{"id": "Q380", "label": "Meta Platforms", "description": "American technology company"}]
                    })
                if search_param == "delta":
                    return httpx.Response(200, json={
                        "search": [
                            {"id": "Q188920", "label": "Delta Air Lines", "description": "American major airline company"},
                            {"id": "Q5254632", "label": "Delta Faucet Company", "description": "American plumbing fixtures company"}
                        ]
                    })
                if "parkedventuretrap" in search_param:
                    return httpx.Response(200, json={
                        "search": [{"id": "Q999999", "label": "ParkedVentureTrap", "description": "company"}]
                    })
                return httpx.Response(200, json={"search": []})

            if "wbgetentities" in url:
                qid = request.url.params.get("ids", "")
                p856_map = {
                    "Q182477": "https://www.nvidia.com/",
                    "Q380": "https://www.meta.com/",
                    "Q188920": "https://www.delta.com/",
                    "Q5254632": "https://www.deltafaucet.com/",
                    "Q999999": "https://parkedventuretrap.com/"
                }
                if qid in p856_map:
                    return httpx.Response(200, json={
                        "entities": {
                            qid: {
                                "claims": {
                                    "P856": [{"mainsnak": {"datavalue": {"value": p856_map[qid]}}}]
                                }
                            }
                        }
                    })
                return httpx.Response(200, json={"entities": {}})

        # 2. SEC EDGAR API
        if "sec.gov/files/company_tickers.json" in url:
            return httpx.Response(200, json={
                "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
                "1": {"cik_str": 1326801, "ticker": "META", "title": "Meta Platforms, Inc."},
            })

        if "data.sec.gov/submissions/CIK0001045810.json" in url:
            return httpx.Response(200, json={
                "cik": "0001045810",
                "name": "NVIDIA CORP",
                "sic": "3674",
                "sicDescription": "SEMICONDUCTORS & RELATED DEVICES",
                "stateOfIncorporation": "DE",
                "tickers": ["NVDA"],
                "exchanges": ["Nasdaq"],
                "addresses": {
                    "business": {"street1": "2788 SAN TOMAS EXPRESSWAY", "city": "SANTA CLARA", "stateOrCountry": "CA", "zipCode": "95051"},
                    "mailing": {"street1": "251 LITTLE FALLS DRIVE", "city": "WILMINGTON", "stateOrCountry": "DE", "zipCode": "19808"}
                },
                "filings": {"recent": {"form": ["10-K"], "filingDate": ["2026-02-20"]}}
            })

        if "data.sec.gov/submissions/CIK0001326801.json" in url:
            return httpx.Response(200, json={
                "cik": "0001326801",
                "name": "Meta Platforms, Inc.",
                "sic": "7370",
                "sicDescription": "SERVICES-COMPUTER PROGRAMMING, DATA PROCESSING, ETC.",
                "stateOfIncorporation": "DE",
                "tickers": ["META"],
                "exchanges": ["Nasdaq"],
                "formerNames": [{"name": "Facebook, Inc."}],
                "addresses": {
                    "business": {"street1": "1 META WAY", "city": "MENLO PARK", "stateOrCountry": "CA", "zipCode": "94025"},
                    "mailing": {"street1": "1 META WAY", "city": "MENLO PARK", "stateOrCountry": "CA", "zipCode": "94025"}
                },
                "filings": {"recent": {"form": ["10-K"], "filingDate": ["2026-01-30"]}}
            })

        if "data.sec.gov/submissions/CIK0000806085.json" in url:
            return httpx.Response(200, json={
                "cik": "0000806085",
                "name": "LEHMAN BROTHERS HOLDINGS INC",
                "sic": "6211",
                "stateOfIncorporation": "DE",
                "tickers": [],
                "exchanges": [],
                "addresses": {
                    "business": {"street1": "745 SEVENTH AVENUE", "city": "NEW YORK", "stateOrCountry": "NY", "zipCode": "10019"}
                },
                "filings": {"recent": {"form": ["15-12G"], "filingDate": ["2012-03-06"]}}
            })

        # 3. GLEIF LEI API
        if "api.gleif.org/api/v1/lei-records/5493006MHB84DD0ZWV18" in url:
            return httpx.Response(200, json={
                "data": {
                    "id": "5493006MHB84DD0ZWV18",
                    "attributes": {
                        "lei": "5493006MHB84DD0ZWV18",
                        "entity": {
                            "legalName": {"name": "SIEMENS AKTIENGESELLSCHAFT"},
                            "jurisdiction": "DE",
                            "registeredAs": "HRB 6684",
                            "status": "ACTIVE",
                            "legalAddress": {"addressLines": ["Werner-von-Siemens-Straße 1"], "city": "München", "postalCode": "80333", "country": "DE"},
                            "headquartersAddress": {"addressLines": ["Werner-von-Siemens-Straße 1"], "city": "München", "postalCode": "80333", "country": "DE"}
                        },
                        "registration": {"lastUpdateDate": "2026-01-15T00:00:00Z"}
                    }
                }
            })

        if "api.gleif.org" in url:
            return httpx.Response(200, json={"data": []})

        # 4. First-Party Websites
        if "nvidia.com" in url:
            return httpx.Response(200, text="""
                <html><head><title>NVIDIA — World Leader in AI Computing</title></head>
                <body><p>© 2026 NVIDIA Corporation. All rights reserved.</p></body></html>
            """)

        if "meta.com" in url:
            return httpx.Response(200, text="""
                <html><head><title>Meta | Social Metaverse Company</title></head>
                <body><p>© 2026 Meta Platforms, Inc. All rights reserved.</p></body></html>
            """)

        if "stripe.com" in url:
            if "/terms" in url:
                return httpx.Response(200, text="""
                    <html><body><p>Welcome to Stripe. © 2026 Stripe, Inc. All rights reserved.</p></body></html>
                """)
            if "/about" in url:
                return httpx.Response(200, text="""
                    <html><body><p>Founded in 2010, Stripe builds economic infrastructure for the internet.</p></body></html>
                """)
            return httpx.Response(200, text="""
                <html><head><title>Stripe | Financial Infrastructure</title>
                <script type="application/ld+json">
                {
                    "@context": "https://schema.org",
                    "@type": "Corporation",
                    "name": "Stripe",
                    "legalName": "Stripe, Inc.",
                    "foundingDate": "2010",
                    "email": "info@stripe.com",
                    "address": {
                        "@type": "PostalAddress",
                        "addressLocality": "South San Francisco",
                        "addressRegion": "CA",
                        "addressCountry": "USA"
                    }
                }
                </script></head><body><h1>Stripe Financial Infrastructure</h1></body></html>
            """)

        if "delta.com" in url:
            return httpx.Response(200, text="<html><head><title>Delta Air Lines | Flights & Plane Tickets</title></head></html>")

        if "deltafaucet.com" in url:
            return httpx.Response(200, text="<html><head><title>Delta Faucet | Kitchen and Bath Fixtures</title></head></html>")

        if "parkedventuretrap.com" in url:
            return httpx.Response(200, text="<html><head><title>parkedventuretrap.com - Domain For Sale</title></head></html>")

        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def run_benchmark() -> dict[str, Any]:
    golden_path = Path(__file__).parent / "golden_companies.json"
    cases = json.loads(golden_path.read_text(encoding="utf-8"))

    transport = create_benchmark_mock_transport()
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        orchestrator = EnrichmentPipelineOrchestrator(client=client)

        total_cases = len(cases)
        decision_correct = 0
        auto_match_predicted = 0
        auto_match_true_positive = 0
        resolvable_ground_truth = 0

        field_checks = 0
        field_correct = 0
        brier_squared_errors: list[float] = []
        case_reports: list[dict[str, Any]] = []

        for case in cases:
            req = EnrichmentRequest(**case["input"], force_refresh=True)
            exp = case["expected"]
            res = await orchestrator.execute(req)

            exp_decision = exp["match_decision"]
            if exp_decision == "AUTO_MATCH":
                resolvable_ground_truth += 1

            is_decision_match = (res.match_decision.value == exp_decision)
            is_domain_match = (res.canonical_domain == exp.get("canonical_domain"))

            if res.match_decision == MatchDecision.AUTO_MATCH:
                auto_match_predicted += 1
                if is_decision_match and is_domain_match:
                    auto_match_true_positive += 1

            if is_decision_match and is_domain_match:
                decision_correct += 1

            # Evaluate enriched fields when AUTO_MATCH
            this_case_fields_ok = True
            if exp_decision == "AUTO_MATCH":
                if "legal_name" in exp:
                    field_checks += 1
                    actual_ln = (res.data.legal_name or "").rstrip(".")
                    expected_ln = exp["legal_name"].rstrip(".")
                    ok = (actual_ln == expected_ln)
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok
                if "status" in exp:
                    field_checks += 1
                    ok = (res.data.status.value == exp["status"])
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok
                if "founded_year" in exp:
                    field_checks += 1
                    ok = (res.data.founded_year == exp["founded_year"])
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok
                if "hq_city" in exp:
                    field_checks += 1
                    ok = bool(res.data.headquarters and res.data.headquarters.city == exp["hq_city"])
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok
                if "registered_office_city" in exp:
                    field_checks += 1
                    ok = bool(res.data.registered_office and res.data.registered_office.city == exp["registered_office_city"])
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok
                if "cik" in exp:
                    field_checks += 1
                    ok = (res.data.cik == exp["cik"])
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok
                if "lei" in exp:
                    field_checks += 1
                    ok = (res.data.lei == exp["lei"])
                    field_correct += int(ok)
                    this_case_fields_ok = this_case_fields_ok and ok

            # Empirical outcome y_i in {0.0, 1.0} for Brier calibration
            # For AUTO_MATCH cases, y=1.0 if both entity match and all checked fields are correct
            # For UNRESOLVED / NEEDS_REVIEW cases, y=0.0 if it is not an auto-matched entity
            y_true = 1.0 if (exp_decision == "AUTO_MATCH" and is_decision_match and is_domain_match and this_case_fields_ok) else 0.0
            p_pred = res.confidence_score if res.match_decision == MatchDecision.AUTO_MATCH else 0.0
            brier_squared_errors.append((p_pred - y_true) ** 2)

            case_reports.append({
                "case_id": case["case_id"],
                "category": case["category"],
                "expected_decision": exp_decision,
                "actual_decision": res.match_decision.value,
                "canonical_domain": res.canonical_domain,
                "confidence_score": res.confidence_score,
                "confidence_tier": res.confidence_tier.value,
                "passed": is_decision_match and is_domain_match and this_case_fields_ok
            })

        precision = round(auto_match_true_positive / auto_match_predicted, 4) if auto_match_predicted else 1.0
        recall = round(auto_match_true_positive / resolvable_ground_truth, 4) if resolvable_ground_truth else 1.0
        field_accuracy = round(field_correct / field_checks, 4) if field_checks else 1.0
        brier_score = round(sum(brier_squared_errors) / len(brier_squared_errors), 4)

        return {
            "total_cases": total_cases,
            "decision_accuracy": round(decision_correct / total_cases, 4),
            "auto_match_precision": precision,
            "auto_match_recall": recall,
            "field_level_accuracy": field_accuracy,
            "brier_calibration_score": brier_score,
            "cases": case_reports
        }


if __name__ == "__main__":
    summary = asyncio.run(run_benchmark())
    print(json.dumps(summary, indent=2))
