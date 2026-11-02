import logging
import re
from typing import Optional

from app.models.company import CompanyData
from app.models.provenance import FieldProvenance, SourceType

logger = logging.getLogger(__name__)


class GroundingAuditor:
    """
    Stage 4: Structured Grounding Audit & Hallucination Guard
    Verifies that all extracted quotes, citations, and textual assertions
    are strict substrings of the raw scraped ground-truth corpus.
    Rejects or penalizes any hallucinated or unverified fields.
    """

    @staticmethod
    def normalize_for_matching(text: str) -> str:
        """
        Normalizes whitespace and casing for substring matching without altering semantics.
        """
        if not text:
            return ""
        # Remove extra whitespace and special non-breaking characters
        s = re.sub(r"\s+", " ", text)
        return s.strip().lower()

    @classmethod
    def verify_grounding(cls, citation_quote: Optional[str], raw_corpus: str) -> bool:
        """
        Checks whether the citation quote exists verbatim in the scraped raw text.
        """
        if not citation_quote:
            return False
        if not raw_corpus:
            return False

        norm_quote = cls.normalize_for_matching(citation_quote)
        norm_corpus = cls.normalize_for_matching(raw_corpus)

        # Allow matching if quote is a verbatim substring
        return norm_quote in norm_corpus

    @classmethod
    def audit_provenance(
        cls,
        data: CompanyData,
        provenance: dict[str, FieldProvenance],
        raw_corpus: str
    ) -> list[str]:
        """
        Audits all first-party extracted claims.
        Returns list of fields that failed grounding audit.
        """
        audit_failures: list[str] = []

        # Sources that require verbatim presence in scraped ground-truth text
        GROUNDED_SOURCES = {
            SourceType.FIRST_PARTY_TERMS.value,
            SourceType.FIRST_PARTY_ABOUT.value,
            SourceType.FIRST_PARTY_CONTACT.value,
            SourceType.FIRST_PARTY_HOMEPAGE.value,
        }

        for field, prov in list(provenance.items()):
            if prov.source_type in GROUNDED_SOURCES:
                quote = prov.exact_quote
                if not quote or not cls.verify_grounding(quote, raw_corpus):
                    logger.warning(
                        f"Grounding Audit FAILURE for field '{field}': "
                        f"Citation '{quote}' was not found in raw corpus."
                    )
                    audit_failures.append(field)
                    # Severely penalize confidence and flag ungrounded claim
                    prov.confidence = round(prov.confidence * 0.4, 2)
                    prov.exact_quote = f"[UNGROUNDED/AUDIT_FAILED]: {quote}"

        return audit_failures

