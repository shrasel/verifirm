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
      },
      "telephone": "+1-888-926-2266",
      "email": "info@stripe.com"
    }
    </script>
</head>
<body>
    <header><h1>Stripe Infrastructure</h1></header>
    <main>
        <p>Founded in 2010 by Patrick and John Collison.</p>
        <p>Contact our support team at <a href="mailto:support@stripe.com">support@stripe.com</a> or call <a href="tel:+18889262266">+1 (888) 926-2266</a>.</p>
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

    assert "phone" in evidence_by_field
    assert evidence_by_field["phone"].value == "+1-888-926-2266"

    assert "email" in evidence_by_field
    assert evidence_by_field["email"].value == "info@stripe.com"


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

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    emails = crawler.extract_contact_emails(soup, clean_text, "stripe.com")
    assert len(emails) >= 1
    assert any("support@stripe.com" in mail for mail, _ in emails)

    phones = crawler.extract_phone_numbers(soup, clean_text)
    assert len(phones) >= 1
    assert any("888" in phone for phone, _ in phones)

