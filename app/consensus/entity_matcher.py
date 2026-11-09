import re
from typing import Optional

from app.models.company import HeadquartersAddress, LegalEntityRecord, WebsiteAssertion
from app.models.provenance import FieldProvenance, SourceFamily, get_source_family
from app.models.request import EnrichmentRequest
from app.models.response import CandidateEntity, MatchDecision


class ProbabilisticEntityMatcher:
    """
    Fellegi-Sunter inspired probabilistic record linkage and three-way decision engine.
    Evaluates candidate entities across multiple comparison features and classifies matches into:
      - AUTO_MATCH: High calibrated probability (>= 0.75) with clear separation margin (>= 0.12)
      - NEEDS_REVIEW: Plausible match (0.45 - 0.74) or competing homonyms within margin
      - UNRESOLVED: Weak or insufficient evidence (< 0.45); abstains rather than guessing
    """

    AUTO_MATCH_THRESHOLD: float = 0.75
    REVIEW_THRESHOLD: float = 0.45
    COMPETING_MARGIN: float = 0.12

    LEGAL_DESIGNATIONS = re.compile(
        r"\b(inc|incorporated|llc|l\.l\.c\.|corp|corporation|ltd|limited|plc|co|company|"
        r"gmbh|ag|s\.a\.|b\.v\.|n\.v\.|pte|pty|holdings?|group|international)\b[.,]?",
        re.IGNORECASE
    )

    @classmethod
    def normalize_entity_tokens(cls, name: Optional[str]) -> list[str]:
        if not name:
            return []
        cleaned = cls.LEGAL_DESIGNATIONS.sub(" ", name.lower())
        cleaned = re.sub(r"[^\w\s]", " ", cleaned)
        return [t for t in cleaned.split() if len(t) > 1 or t.isalnum()]

    @classmethod
    def compute_name_similarity(
        cls,
        query_name: Optional[str],
        candidate_name: Optional[str],
        former_names: Optional[list[str]] = None
    ) -> float:
        """
        Computes token-level and prefix similarity between a query name and a candidate name
        (including any known former corporate names).
        """
        if not query_name or not candidate_name:
            return 0.0

        q_tokens = cls.normalize_entity_tokens(query_name)
        c_tokens = cls.normalize_entity_tokens(candidate_name)

        if not q_tokens or not c_tokens:
            return 0.0

        if q_tokens == c_tokens:
            return 1.0

        q_set = set(q_tokens)
        c_set = set(c_tokens)
        intersection = q_set & c_set
        union = q_set | c_set
        jaccard = len(intersection) / len(union) if union else 0.0

        # Boost if primary leading token matches and one is a subset of the other
        # e.g., "Meta" vs "Meta Platforms", "Stripe" vs "Stripe Payments"
        lead_match = (q_tokens[0] == c_tokens[0])
        subset_match = q_set.issubset(c_set) or c_set.issubset(q_set)

        score = jaccard
        if lead_match and subset_match:
            score = max(score, 0.88)
        elif lead_match and len(intersection) >= 1:
            score = max(score, 0.72)

        # Also check former names if provided
        if former_names:
            for fn in former_names:
                fn_sim = cls.compute_name_similarity(query_name, fn, former_names=None)
                if fn_sim > score:
                    score = fn_sim

        return round(min(1.0, score), 3)

    @classmethod
    def check_jurisdiction_compatibility(
        cls,
        hint: Optional[str],
        candidate_jurisdiction: Optional[str]
    ) -> Optional[bool]:
        """
        Returns True if compatible, False if contradictory, None if either is missing.
        """
        if not hint or not candidate_jurisdiction:
            return None

        h = hint.strip().upper()
        c = candidate_jurisdiction.strip().upper()

        # Normalize common country names to ISO prefixes
        ALIASES = {
            "USA": "US",
            "UNITED STATES": "US",
            "UK": "GB",
            "UNITED KINGDOM": "GB",
            "GREAT BRITAIN": "GB",
            "GERMANY": "DE",
            "FRANCE": "FR",
            "CANADA": "CA",
            "DELAWARE": "US-DE",
            "CALIFORNIA": "US-CA",
            "NEW YORK": "US-NY",
        }
        h_norm = ALIASES.get(h, h)
        c_norm = ALIASES.get(c, c)

        if h_norm == c_norm:
            return True
        # Country prefix match (e.g., 'US' matches 'US-DE')
        if h_norm.startswith(c_norm + "-") or c_norm.startswith(h_norm + "-"):
            return True
        return False

    @classmethod
    def decide_match(cls, candidates: list[CandidateEntity]) -> MatchDecision:
        """
        Applies the Fellegi-Sunter three-way decision rule over ranked candidates.
        """
        if not candidates:
            return MatchDecision.UNRESOLVED

        sorted_cands = sorted(candidates, key=lambda c: c.score, reverse=True)
        top = sorted_cands[0]

        if top.score < cls.REVIEW_THRESHOLD:
            return MatchDecision.UNRESOLVED

        # Check for competing distinct candidates within margin
        for runner_up in sorted_cands[1:]:
            distinct_entity = (
                (top.domain and runner_up.domain and top.domain != runner_up.domain)
                or (top.cik and runner_up.cik and top.cik != runner_up.cik)
                or (top.registration_number and runner_up.registration_number and top.registration_number != runner_up.registration_number)
            )
            if distinct_entity and runner_up.score >= 0.55 and (top.score - runner_up.score) < cls.COMPETING_MARGIN:
                return MatchDecision.NEEDS_REVIEW

        if top.score >= cls.AUTO_MATCH_THRESHOLD:
            return MatchDecision.AUTO_MATCH

        return MatchDecision.NEEDS_REVIEW

    @classmethod
    def evaluate_website_assertion(
        cls,
        canonical_domain: Optional[str],
        is_online: bool,
        http_status: Optional[int],
        legal_entity: Optional[LegalEntityRecord],
        operating_hq: Optional[HeadquartersAddress],
        provenance: dict[str, FieldProvenance],
        candidates: list[CandidateEntity]
    ) -> WebsiteAssertion:
        """
        Evaluates independent evidence linking the website domain to the legal entity.
        """
        if not canonical_domain:
            return WebsiteAssertion(
                canonical_domain=None,
                website_url=None,
                is_online=False,
                http_status=None,
                linkage_signals=[],
                verification_score=0.0,
                is_independently_verified=False
            )

        signals: list[str] = []
        score = 0.0

        # 1. Check if legal_name was corroborated across both Official Registry and First-Party Web
        legal_prov = provenance.get("legal_name")
        if legal_prov:
            if legal_prov.independent_sources_count >= 2:
                signals.append("cross_source_legal_name_corroboration")
                score += 0.45
            elif get_source_family(legal_prov.source_type.value) == SourceFamily.FIRST_PARTY_WEB:
                signals.append("first_party_terms_or_jsonld_legal_name")
                score += 0.30
            elif get_source_family(legal_prov.source_type.value) == SourceFamily.OFFICIAL_REGISTRY:
                signals.append("official_registry_legal_entity_verified")
                score += 0.25

        # 2. Check if headquarters or phone has independent corroboration
        hq_prov = provenance.get("headquarters")
        if hq_prov and hq_prov.independent_sources_count >= 2:
            signals.append("address_corroborated_with_registry")
            score += 0.25
        elif (
            legal_entity
            and legal_entity.registered_office
            and operating_hq
            and legal_entity.registered_office.city
            and operating_hq.city
            and legal_entity.registered_office.city.lower() == operating_hq.city.lower()
        ):
            signals.append("registered_and_operating_city_match")
            score += 0.20

        phone_prov = provenance.get("phone")
        if phone_prov and phone_prov.independent_sources_count >= 2:
            signals.append("phone_corroborated_with_registry")
            score += 0.20

        # 3. Check candidate discovery signals (e.g. Wikidata P856 or landing page verification)
        for c in candidates:
            if c.domain == canonical_domain:
                for sig in c.supporting_signals:
                    if sig not in signals:
                        signals.append(sig)
                if "wikidata_p856_candidate" in c.supporting_signals:
                    score += 0.20
                if "landing_page_title_match" in c.supporting_signals or "landing_page_jsonld_match" in c.supporting_signals:
                    score += 0.20
                break

        if is_online:
            signals.append("http_live_response")
            score += 0.15

        final_score = round(min(1.0, score), 2)
        is_verified = final_score >= 0.50 and is_online

        return WebsiteAssertion(
            canonical_domain=canonical_domain,
            website_url=f"https://{canonical_domain}",
            is_online=is_online,
            http_status=http_status,
            linkage_signals=signals,
            verification_score=final_score,
            is_independently_verified=is_verified
        )

