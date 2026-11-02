import pytest
from app.fetchers.base import RawEvidenceBag
from app.fetchers.jsonld_extractor import JsonLdExtractor
from app.fetchers.web_crawler import FirstPartyWebCrawler


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Stripe | Financial Infrastructure for the Internet</title>
    <meta name="description" content="Stripe powers online and in-person payments for businesses around the world.">
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Corporation",
      "name": "Stripe",
      "legalName": "Stripe, Inc.",
      "url": "https://stripe.com",
      "foundingDate": "2010",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "354 Oyster Point Blvd",
        "addressLocality": "South San Francisco",
        "addressRegion": "CA",
        "postalCode": "94080",
        "addressCountry": "USA"
      },
      "numberOfEmployees": {
        "@type": "QuantitativeValue",
        "value": "7000+"
      }
    }
    </script>
</head>
<body>
    <header><h1>Stripe Infrastructure</h1></header>
    <main>
        <p>Founded in 2010 by Patrick and John Collison.</p>
    </main>
    <footer>
        <p>&copy; 2026 Stripe Payments Company Inc. All rights reserved.</p>
    </footer>
</body>
</html>
"""


def test_jsonld_extractor():
    bag = RawEvidenceBag(canonical_domain="stripe.com")
    entities = JsonLdExtractor.extract_from_html(SAMPLE_HTML, "https://stripe.com", bag)

    assert len(entities) == 1
    evidence_by_field = {e.field: e for e in bag.extracted_evidence}

    assert "legal_name" in evidence_by_field
    assert evidence_by_field["legal_name"].value == "Stripe, Inc."
    assert evidence_by_field["legal_name"].confidence >= 0.95

    assert "founded_year" in evidence_by_field
    assert evidence_by_field["founded_year"].value == 2010

    assert "headquarters" in evidence_by_field
    assert evidence_by_field["headquarters"].value["city"] == "South San Francisco"
    assert evidence_by_field["headquarters"].value["state"] == "CA"


def test_web_crawler_extractors():
    crawler = FirstPartyWebCrawler()
    clean_text = crawler.clean_html_to_text(SAMPLE_HTML)

    legal_res = crawler.extract_copyright_legal_name(clean_text)
    assert legal_res is not None
    name, quote = legal_res
    assert "Stripe Payments Company Inc" in name

    founded_res = crawler.extract_founded_year(clean_text)
    assert founded_res is not None
    year, f_quote = founded_res
    assert year == 2010
