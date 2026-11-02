import logging
from typing import Any, Optional
from datetime import datetime, timezone

from app.consensus.authority_matrix import AuthorityMatrix
from app.fetchers.base import ExtractedEvidence, RawEvidenceBag
from app.models.company import CompanyData, HeadquartersAddress, IndustryTaxonomy, OperatingStatus
from app.models.provenance import Discrepancy, FieldProvenance, SourceType
from app.models.response import ConfidenceTier

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
    Stage 3: Reconciliation & Consensus Engine
    Cross-verifies claims, executes source authority tie-breakers,
    calculates field-level confidence, and generates audit trail envelopes.
    """

    def __init__(self):
        self.authority_matrix = AuthorityMatrix()

    def _normalize_comparable(self, field: str, value: Any) -> str:
        """
        Normalizes values to test equivalence across varying formats.
        """
        if value is None:
            return ""
        if isinstance(value, dict):
            # Normalize address or dictionary
            vals = [str(v).strip().lower() for k, v in sorted(value.items()) if v and k != "formatted_address"]
            return " ".join(vals)
        s = str(value).strip().lower()
        # Strip trailing legal designations for name comparison
        if field == "legal_name":
            import re
            s = re.sub(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|company|co)\b[.,]?", "", s)
            s = re.sub(r"[^\w\s]", "", s)
        return " ".join(s.split())

    def reconcile_field(self, field: str, candidates: list[ExtractedEvidence]) -> Optional[ReconciledFieldResult]:
        if not candidates:
            return None

        # Group by normalized value to detect multi-source agreement
        value_groups: dict[str, list[ExtractedEvidence]] = {}
        for c in candidates:
            norm_key = self._normalize_comparable(field, c.value)
            if norm_key not in value_groups:
                value_groups[norm_key] = []
            value_groups[norm_key].append(c)

        # Sort candidate claims by their intrinsic source authority weight
        sorted_candidates = sorted(
            candidates,
            key=lambda c: self.authority_matrix.get_source_weight(field, c.source_type),
            reverse=True
        )

        winning_claim = sorted_candidates[0]
        winning_norm = self._normalize_comparable(field, winning_claim.value)
        agreeing_claims = value_groups.get(winning_norm, [])

        # Determine consensus multiplier
        discrepancy = None
        has_conflicts = len(value_groups) > 1

        if has_conflicts:
            # Competing distinct values detected!
            competing = [
                {
                    "source": c.source_name,
                    "source_type": c.source_type,
                    "value": c.value,
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
        elif len(agreeing_claims) >= 2:
            # Multi-source independent consensus!
            consensus_multiplier = 1.00
        else:
            # Single credible source without opposing evidence
            consensus_multiplier = 0.88

        source_weight = self.authority_matrix.get_source_weight(field, winning_claim.source_type)
        final_confidence = round(min(1.0, source_weight * consensus_multiplier), 3)

        prov = FieldProvenance(
            source=winning_claim.source_name,
            source_type=winning_claim.source_type,
            confidence=final_confidence,
            exact_quote=winning_claim.verbatim_quote,
            filing_id=winning_claim.filing_id,
            verified_at=winning_claim.retrieved_at
        )

        return ReconciledFieldResult(
            field=field,
            value=winning_claim.value,
            provenance=prov,
            discrepancy=discrepancy
        )

    def reconcile_all(
        self,
        evidence_bag: RawEvidenceBag,
        active_web_presence: bool = True
    ) -> tuple[CompanyData, dict[str, FieldProvenance], list[Discrepancy], float, ConfidenceTier]:
        """
        Reconciles all harvested evidence into final CompanyData, provenance envelope,
        and composite confidence score.
        """
        # Group raw evidence by field
        evidence_by_field: dict[str, list[ExtractedEvidence]] = {}
        for ev in evidence_bag.extracted_evidence:
            if ev.field not in evidence_by_field:
                evidence_by_field[ev.field] = []
            evidence_by_field[ev.field].append(ev)

        data = CompanyData(
            website=f"https://{evidence_bag.canonical_domain}",
            active_web_presence=active_web_presence
        )
        provenance: dict[str, FieldProvenance] = {}
        discrepancies: list[Discrepancy] = []

        # If active web presence is verified and status wasn't already in evidence, add active presence evidence
        if "status" not in evidence_by_field and active_web_presence:
            now_iso = datetime.now(timezone.utc).isoformat()
            evidence_by_field["status"] = [
                ExtractedEvidence(
                    field="status",
                    value="Active",
                    source_name=f"https://{evidence_bag.canonical_domain} (HTTP/DNS Probe)",
                    source_type=SourceType.FIRST_PARTY_HOMEPAGE.value,
                    confidence=0.92,
                    retrieved_at=now_iso
                )
            ]

        # Target fields to reconcile
        target_fields = [
            "legal_name", "brand_name", "status", "founded_year",
            "headquarters", "employee_range", "industry", "description"
        ]

        weighted_score_sum = 0.0
        populated_weights = 0.0

        for field in target_fields:
            weight = self.authority_matrix.FIELD_WEIGHTS.get(field, 0.1)
            claims = evidence_by_field.get(field, [])
            res = self.reconcile_field(field, claims)

            if res:
                provenance[field] = res.provenance
                if res.discrepancy:
                    discrepancies.append(res.discrepancy)

                # Assign reconciled value to data model
                if field == "legal_name":
                    data.legal_name = str(res.value)
                elif field == "brand_name":
                    data.brand_name = str(res.value)
                elif field == "status":
                    data.status = OperatingStatus(res.value) if res.value in OperatingStatus._value2member_map_ else OperatingStatus.ACTIVE
                elif field == "founded_year":
                    data.founded_year = int(res.value)
                elif field == "headquarters":
                    if isinstance(res.value, dict):
                        data.headquarters = HeadquartersAddress(**res.value)
                elif field == "employee_range":
                    data.employee_range = str(res.value)
                elif field == "industry":
                    if isinstance(res.value, dict):
                        data.industry = IndustryTaxonomy(**res.value)
                elif field == "description":
                    data.description = str(res.value)

                weighted_score_sum += res.provenance.confidence * weight
                populated_weights += weight

        # If brand name is missing, infer from domain or legal name
        if not data.brand_name:
            if evidence_bag.brand_name:
                data.brand_name = evidence_bag.brand_name
            elif data.legal_name:
                data.brand_name = data.legal_name.split(",")[0].split()[0]
            else:
                data.brand_name = evidence_bag.canonical_domain.split(".")[0].capitalize()

        # Compute composite confidence score over populated fields
        composite_score = round(weighted_score_sum / populated_weights, 3) if populated_weights > 0 else 0.5

        # Determine Confidence Tier
        if composite_score >= 0.95:
            tier = ConfidenceTier.HIGH_ASSURANCE
        elif composite_score >= 0.70:
            tier = ConfidenceTier.MODERATE_ASSURANCE
        else:
            tier = ConfidenceTier.LOW_ASSURANCE

        return data, provenance, discrepancies, composite_score, tier
