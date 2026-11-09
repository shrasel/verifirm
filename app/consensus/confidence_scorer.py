import logging
import math
import re
from typing import Any, Optional

from app.consensus.authority_matrix import AuthorityMatrix
from app.consensus.entity_matcher import ProbabilisticEntityMatcher
from app.fetchers.base import ExtractedEvidence, RawEvidenceBag
from app.models.company import (
    BrandProfile,
    CompanyData,
    HeadquartersAddress,
    IndustryTaxonomy,
    LegalEntityRecord,
    OperatingStatus,
    SecurityListing,
)
from app.models.provenance import Discrepancy, FieldProvenance, get_source_family
from app.models.response import CandidateEntity, ConfidenceTier
from app.pipeline.stage4_grounding import GroundingAuditor

logger = logging.getLogger(__name__)


class ReconciledFieldResult:
    def __init__(
        self,
        field: str,
        value: Any,
        provenance: FieldProvenance,
        discrepancy: Optional[Discrepancy] = None
    ):
        self.field = field
        self.value = value
        self.provenance = provenance
        self.discrepancy = discrepancy


class ConsensusAndScoringEngine:
    """
    Stage 3: Reconciliation, Source-Independence & Calibrated Scoring Engine
    - Incorporates both AuthorityMatrix weights and each ExtractedEvidence item's own confidence.
    - Enforces true SourceFamily independence before awarding cross-source consensus boosts.
    - Separates Registered Office vs. Operating Headquarters and Legal Status vs. Website Availability.
    - Applies a field-completeness factor so sparse records cannot artificially score >= 0.95.
    - Executes grounding verification before final score computation.
    """

    def __init__(self):
        self.authority_matrix = AuthorityMatrix()

    def _normalize_comparable(self, field: str, value: Any) -> str:
        """
        Normalizes values to test equivalence across varying formats without mutating the stored value.
        """
        if value is None:
            return ""
        if isinstance(value, dict):
            vals = [str(v).strip().lower() for k, v in sorted(value.items()) if v and k != "formatted_address"]
            return " ".join(vals)
        s = str(value).strip().lower()
        if field == "legal_name":
            s = re.sub(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|plc|company|co)\b[.,]?", "", s)
            s = re.sub(r"[^\w\s]", "", s)
        elif field == "phone":
            digits = re.sub(r"\D", "", s)
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            return digits
        elif field == "email":
            return s.strip().lower()
        return " ".join(s.split())

    def _effective_claim_score(self, field: str, claim: ExtractedEvidence) -> float:
        """
        Combines the field-specific source authority weight with the extractor's own confidence.
        """
        authority_weight = self.authority_matrix.get_source_weight(field, claim.source_type)
        claim_conf = max(0.0, min(1.0, float(claim.confidence)))
        return round(min(claim_conf, math.sqrt(authority_weight * claim_conf)), 4)

    def reconcile_field(self, field: str, candidates: list[ExtractedEvidence]) -> Optional[ReconciledFieldResult]:
        if not candidates:
            return None

        # Group by normalized value to detect agreement
        value_groups: dict[str, list[ExtractedEvidence]] = {}
        for c in candidates:
            norm_key = self._normalize_comparable(field, c.value)
            if norm_key not in value_groups:
                value_groups[norm_key] = []
            value_groups[norm_key].append(c)

        # Sort candidate claims by effective score (authority weight + claim confidence)
        sorted_candidates = sorted(
            candidates,
            key=lambda c: (
                self.authority_matrix.get_source_weight(field, c.source_type),
                c.confidence
            ),
            reverse=True
        )

        winning_claim = sorted_candidates[0]
        winning_norm = self._normalize_comparable(field, winning_claim.value)
        agreeing_claims = value_groups.get(winning_norm, [])

        # Count distinct independent source families agreeing on the winning value
        agreeing_families = {get_source_family(c.source_type) for c in agreeing_claims}
        independent_family_count = len(agreeing_families)

        discrepancy = None
        has_conflicts = len(value_groups) > 1

        if has_conflicts:
            competing = [
                {
                    "source": c.source_name,
                    "source_type": c.source_type,
                    "value": c.value,
                    "claim_confidence": c.confidence,
                    "authority_weight": self.authority_matrix.get_source_weight(field, c.source_type)
                }
                for c in sorted_candidates
            ]
            rationale = (
                f"Resolved using Authority Matrix: selected '{winning_claim.source_type}' "
                f"(source: {winning_claim.source_name}) over secondary conflicting sources."
            )
            discrepancy = Discrepancy(
                field=field,
                competing_values=competing,
                resolution_rationale=rationale
            )
            consensus_multiplier = 0.75
        elif independent_family_count >= 2:
            # True cross-source consensus across independent families (e.g., Registry + Website)
            consensus_multiplier = 1.02
        elif len(agreeing_claims) >= 2:
            # Within-family corroboration (e.g., JSON-LD on '/' + text on '/about' from same domain)
            consensus_multiplier = 0.99
        else:
            # Single uncorroborated claim
            consensus_multiplier = 0.88

        base_score = self._effective_claim_score(field, winning_claim)
        final_confidence = round(min(1.0, base_score * consensus_multiplier), 3)

        prov = FieldProvenance(
            source=winning_claim.source_name,
            source_type=winning_claim.source_type,
            confidence=final_confidence,
            exact_quote=winning_claim.verbatim_quote,
            filing_id=winning_claim.filing_id,
            verified_at=winning_claim.retrieved_at,
            source_updated_at=winning_claim.source_updated_at,
            raw_claim=winning_claim.raw_claim if winning_claim.raw_claim is not None else winning_claim.value,
            transformation_applied=winning_claim.transformation_applied,
            independent_sources_count=independent_family_count
        )

        return ReconciledFieldResult(
            field=field,
            value=winning_claim.value,
            provenance=prov,
            discrepancy=discrepancy
        )

    def compute_composite_score_and_tier(
        self,
        provenance: dict[str, FieldProvenance]
    ) -> tuple[float, ConfidenceTier]:
        """
        Computes the calibrated overall confidence score and tier from field-level provenance,
        accounting for field completeness/coverage and grounding audit penalties.
        """
        if not provenance:
            return 0.0, ConfidenceTier.UNRESOLVED

        weighted_score_sum = 0.0
        populated_weights = 0.0

        for field, prov in provenance.items():
            weight = self.authority_matrix.FIELD_WEIGHTS.get(field, 0.05)
            weighted_score_sum += prov.confidence * weight
            populated_weights += weight

        if populated_weights <= 0.0:
            return 0.0, ConfidenceTier.UNRESOLVED

        raw_average = weighted_score_sum / populated_weights

        # Completeness adjustment: prevent sparse 1-field records from inflating to HIGH_ASSURANCE
        # Full weight saturation occurs at populated_weights >= 0.40 (e.g. legal_name + hq + founded_year)
        completeness_ratio = min(1.0, populated_weights / 0.40)
        coverage_factor = 0.88 + (0.12 * completeness_ratio)

        composite_score = round(min(1.0, raw_average * coverage_factor), 3)

        if composite_score >= 0.90 and len(provenance) >= 2:
            tier = ConfidenceTier.HIGH_ASSURANCE
        elif composite_score >= 0.70:
            tier = ConfidenceTier.MODERATE_ASSURANCE
        else:
            tier = ConfidenceTier.LOW_ASSURANCE

        return composite_score, tier

    def _populate_structured_entities(
        self,
        data: CompanyData,
        evidence_bag: RawEvidenceBag,
        provenance: dict[str, FieldProvenance],
        candidates: Optional[list[CandidateEntity]] = None
    ):
        """
        Populates distinct LegalEntityRecord, BrandProfile, WebsiteAssertion, and SecurityListing models.
        """
        former_names: list[str] = []
        securities: list[SecurityListing] = []
        cik = data.cik or evidence_bag.cik
        lei = data.lei or evidence_bag.lei
        reg_no = data.registration_number or evidence_bag.registration_number
        jurisdiction = data.jurisdiction or evidence_bag.jurisdiction_hint
        inc_date: Optional[str] = str(data.founded_year) if data.founded_year else None

        for filing in evidence_bag.registry_filings:
            if not isinstance(filing, dict):
                continue
            if filing.get("cik") and not cik:
                cik = str(filing["cik"]).zfill(10)
            if filing.get("lei") and not lei:
                lei = str(filing["lei"])
            if filing.get("company_number") and not reg_no:
                reg_no = str(filing["company_number"])
            if filing.get("registration_number") and not reg_no:
                reg_no = str(filing["registration_number"])
            if filing.get("jurisdiction") and not jurisdiction:
                jurisdiction = str(filing["jurisdiction"])
            if filing.get("stateOfIncorporation") and not jurisdiction:
                jurisdiction = f"US-{filing['stateOfIncorporation']}"

            # Extract SEC former names
            for fn in filing.get("formerNames", []):
                if isinstance(fn, dict) and fn.get("name"):
                    former_names.append(str(fn["name"]))

            # Extract SEC tickers & exchanges
            tickers = filing.get("tickers", [])
            exchanges = filing.get("exchanges", [])
            if isinstance(tickers, list):
                for idx, t in enumerate(tickers):
                    if t:
                        exch = exchanges[idx] if isinstance(exchanges, list) and idx < len(exchanges) else None
                        securities.append(SecurityListing(ticker=str(t), exchange=exch, cik=cik))
            elif filing.get("ticker"):
                securities.append(SecurityListing(ticker=str(filing["ticker"]), cik=cik))

        data.cik = cik
        data.lei = lei
        data.registration_number = reg_no
        data.jurisdiction = jurisdiction
        data.securities = securities

        data.legal_entity = LegalEntityRecord(
            registered_name=data.legal_name,
            jurisdiction=jurisdiction,
            registration_number=reg_no,
            lei=lei,
            cik=cik,
            legal_status=data.status,
            incorporation_date=inc_date,
            registered_office=data.registered_office,
            former_names=former_names
        )

        data.brand_profile = BrandProfile(
            brand_name=data.brand_name,
            operating_headquarters=data.headquarters,
            phone=data.phone,
            email=data.email,
            employee_range=data.employee_range,
            description=data.description
        )

        data.website_assertion = ProbabilisticEntityMatcher.evaluate_website_assertion(
            canonical_domain=evidence_bag.canonical_domain,
            is_online=data.active_web_presence,
            http_status=evidence_bag.http_status,
            legal_entity=data.legal_entity,
            operating_hq=data.headquarters,
            provenance=provenance,
            candidates=candidates or []
        )

    def reconcile_all(
        self,
        evidence_bag: RawEvidenceBag,
        active_web_presence: bool = True,
        candidates: Optional[list[CandidateEntity]] = None
    ) -> tuple[CompanyData, dict[str, FieldProvenance], list[Discrepancy], float, ConfidenceTier]:
        """
        Reconciles all harvested evidence into final CompanyData, provenance envelope,
        executes grounding verification, and computes the calibrated confidence score.
        Never conflates website liveness (active_web_presence) with statutory OperatingStatus.
        """
        evidence_by_field: dict[str, list[ExtractedEvidence]] = {}
        for ev in evidence_bag.extracted_evidence:
            if ev.field not in evidence_by_field:
                evidence_by_field[ev.field] = []
            evidence_by_field[ev.field].append(ev)

        website_url = f"https://{evidence_bag.canonical_domain}" if evidence_bag.canonical_domain else None
        data = CompanyData(
            website=website_url,
            status=OperatingStatus.UNKNOWN,
            active_web_presence=bool(evidence_bag.canonical_domain and active_web_presence)
        )
        provenance: dict[str, FieldProvenance] = {}
        discrepancies: list[Discrepancy] = []

        target_fields = [
            "legal_name", "brand_name", "status", "founded_year",
            "headquarters", "registered_office", "phone", "email",
            "employee_range", "industry", "description"
        ]

        for field in target_fields:
            claims = evidence_by_field.get(field, [])
            res = self.reconcile_field(field, claims)

            if res:
                provenance[field] = res.provenance
                if res.discrepancy:
                    discrepancies.append(res.discrepancy)

                if field == "legal_name":
                    data.legal_name = str(res.value)
                elif field == "brand_name":
                    data.brand_name = str(res.value)
                elif field == "status":
                    data.status = (
                        OperatingStatus(res.value)
                        if res.value in OperatingStatus._value2member_map_
                        else OperatingStatus.UNKNOWN
                    )
                elif field == "founded_year":
                    data.founded_year = int(res.value)
                elif field == "headquarters":
                    if isinstance(res.value, dict):
                        data.headquarters = HeadquartersAddress(**res.value)
                elif field == "registered_office":
                    if isinstance(res.value, dict):
                        data.registered_office = HeadquartersAddress(**res.value)
                elif field == "phone":
                    data.phone = str(res.value)
                elif field == "email":
                    data.email = str(res.value)
                elif field == "employee_range":
                    data.employee_range = str(res.value)
                elif field == "industry":
                    if isinstance(res.value, dict):
                        data.industry = IndustryTaxonomy(**res.value)
                elif field == "description":
                    data.description = str(res.value)

        if not data.brand_name:
            if evidence_bag.brand_name:
                data.brand_name = evidence_bag.brand_name
            elif data.legal_name:
                data.brand_name = data.legal_name.split(",")[0].split()[0]
            elif evidence_bag.canonical_domain:
                data.brand_name = evidence_bag.canonical_domain.split(".")[0].capitalize()

        # Execute Grounding Audit BEFORE final score calculation if a scraped text corpus exists
        if evidence_bag.full_text_corpus:
            GroundingAuditor.audit_provenance(
                data=data,
                provenance=provenance,
                raw_corpus=evidence_bag.full_text_corpus
            )

        # Populate distinct LegalEntityRecord, BrandProfile, WebsiteAssertion, and SecurityListing
        self._populate_structured_entities(
            data=data,
            evidence_bag=evidence_bag,
            provenance=provenance,
            candidates=candidates
        )

        # Compute calibrated composite confidence score and tier AFTER grounding audit
        composite_score, tier = self.compute_composite_score_and_tier(provenance)

        return data, provenance, discrepancies, composite_score, tier
