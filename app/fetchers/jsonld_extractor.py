import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from bs4 import BeautifulSoup

from app.fetchers.base import ExtractedEvidence, RawEvidenceBag
from app.models.company import HeadquartersAddress
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class JsonLdExtractor:
    """
    Extracts structured Schema.org entities (Organization, Corporation, LocalBusiness)
    from HTML <script type="application/ld+json"> blocks.
    """

    TARGET_TYPES = {
        "organization", "corporation", "localbusiness", "financialservice",
        "company", "enterprisegroup", "governmentorganization"
    }

    @classmethod
    def extract_from_html(cls, html: str, source_url: str, bag: RawEvidenceBag) -> list[dict[str, Any]]:
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", type=lambda t: t and "application/ld+json" in t.lower())
        extracted_entities = []

        now_iso = datetime.now(timezone.utc).isoformat()

        for s in scripts:
            content = s.string or s.get_text()
            if not content:
                continue

            try:
                data = json.loads(content.strip())
            except Exception:
                continue

            # Can be a single object, an array of objects, or a graph
            candidates = []
            if isinstance(data, list):
                candidates.extend(data)
            elif isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    candidates.extend(data["@graph"])
                else:
                    candidates.append(data)

            for item in candidates:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    item_type_str = " ".join([str(t).lower() for t in item_type])
                else:
                    item_type_str = str(item_type).lower()

                if any(target in item_type_str for target in cls.TARGET_TYPES):
                    extracted_entities.append(item)
                    bag.jsonld_entities.append(item)

                    # 1. Legal / Trade Name
                    legal_name = item.get("legalName")
                    name = item.get("name")
                    if legal_name:
                        bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="legal_name",
                                value=str(legal_name).strip(),
                                source_name=source_url,
                                source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                confidence=0.96,
                                verbatim_quote=f'"legalName": "{legal_name}"',
                                retrieved_at=now_iso
                            )
                        )
                    if name:
                        bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="brand_name",
                                value=str(name).strip(),
                                source_name=source_url,
                                source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                confidence=0.95,
                                verbatim_quote=f'"name": "{name}"',
                                retrieved_at=now_iso
                            )
                        )

                    # 2. Founding Date
                    founding_date = item.get("foundingDate") or item.get("foundingYear")
                    if founding_date:
                        match = re.search(r"\b(18\d\d|19\d\d|20\d\d)\b", str(founding_date))
                        if match:
                            year = int(match.group(1))
                            bag.extracted_evidence.append(
                                ExtractedEvidence(
                                    field="founded_year",
                                    value=year,
                                    source_name=source_url,
                                    source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                    confidence=0.95,
                                    verbatim_quote=f'"foundingDate": "{founding_date}"',
                                    retrieved_at=now_iso
                                )
                            )

                    # 3. Address
                    raw_addr = item.get("address")
                    if isinstance(raw_addr, dict):
                        hq = HeadquartersAddress(
                            street=raw_addr.get("streetAddress"),
                            city=raw_addr.get("addressLocality"),
                            state=raw_addr.get("addressRegion"),
                            postal_code=raw_addr.get("postalCode"),
                            country=raw_addr.get("addressCountry")
                        )
                        # Formatted address string
                        parts = [
                            hq.street,
                            hq.city,
                            f"{hq.state} {hq.postal_code}".strip() if (hq.state or hq.postal_code) else None,
                            hq.country
                        ]
                        hq.formatted_address = ", ".join([p for p in parts if p])
                        if hq.city or hq.formatted_address:
                            bag.extracted_evidence.append(
                                ExtractedEvidence(
                                    field="headquarters",
                                    value=hq.model_dump(),
                                    source_name=source_url,
                                    source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                    confidence=0.95,
                                    verbatim_quote=json.dumps(raw_addr),
                                    retrieved_at=now_iso
                                )
                            )

                    # 4. Description
                    desc = item.get("description")
                    if desc and isinstance(desc, str):
                        bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="description",
                                value=desc.strip(),
                                source_name=source_url,
                                source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                confidence=0.92,
                                verbatim_quote=desc[:150],
                                retrieved_at=now_iso
                            )
                        )

                    # 5. Number of Employees
                    employees = item.get("numberOfEmployees")
                    if employees:
                        val = None
                        if isinstance(employees, dict):
                            val = employees.get("value") or employees.get("name")
                        else:
                            val = str(employees)
                        if val:
                            bag.extracted_evidence.append(
                                ExtractedEvidence(
                                    field="employee_range",
                                    value=str(val),
                                    source_name=source_url,
                                    source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                    confidence=0.90,
                                    verbatim_quote=str(employees),
                                    retrieved_at=now_iso
                                )
                            )

                    # 6. Telephone / Phone Number
                    phone = item.get("telephone") or item.get("phone")
                    if not phone and "contactPoint" in item:
                        cp = item["contactPoint"]
                        if isinstance(cp, list) and cp:
                            phone = cp[0].get("telephone")
                        elif isinstance(cp, dict):
                            phone = cp.get("telephone")
                    if not phone and isinstance(raw_addr, dict):
                        phone = raw_addr.get("telephone")

                    if phone and isinstance(phone, str) and len(phone.strip()) >= 7:
                        clean_phone = phone.strip()
                        bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="phone",
                                value=clean_phone,
                                source_name=source_url,
                                source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                confidence=0.98,
                                verbatim_quote=f'"telephone": "{clean_phone}"',
                                retrieved_at=now_iso
                            )
                        )

                    # 7. Email Address
                    email = item.get("email")
                    if not email and "contactPoint" in item:
                        cp = item["contactPoint"]
                        if isinstance(cp, list) and cp:
                            email = cp[0].get("email")
                        elif isinstance(cp, dict):
                            email = cp.get("email")
                    if not email and isinstance(raw_addr, dict):
                        email = raw_addr.get("email")

                    if email and isinstance(email, str) and "@" in email:
                        clean_email = email.strip().lower()
                        bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="email",
                                value=clean_email,
                                source_name=source_url,
                                source_type=SourceType.FIRST_PARTY_JSONLD.value,
                                confidence=0.98,
                                verbatim_quote=f'"email": "{clean_email}"',
                                retrieved_at=now_iso
                            )
                        )

        return extracted_entities

