import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
import httpx

from app.consensus.confidence_scorer import ConsensusAndScoringEngine
from app.models.company import CompanyData, OperatingStatus
from app.models.request import EnrichmentRequest
from app.models.response import ConfidenceTier, EnrichmentResponse, MatchDecision
from app.pipeline.stage1_canonical import CanonicalIdentityResolver
from app.pipeline.stage2_fetch import Stage2DataFetcher
from app.pipeline.stage4_grounding import GroundingAuditor
from app.storage.cache import CacheManager

logger = logging.getLogger(__name__)


class EnrichmentPipelineOrchestrator:
    """
    End-to-End Verification Pipeline Orchestrator:
    1. Cache Check
    2. Stage 1: Cautious Identity & Domain Resolution (3-Way Decision: AUTO_MATCH, NEEDS_REVIEW, UNRESOLVED)
    3. Stage 2: Parallel Multi-Tier Data Fetching (Web + Official Registries + Optional Vendors)
    4. Stage 3 & 4: Pre-Score Grounding Audit + Independent Source Reconciliation & Calibrated Scoring
    5. Response Packaging & Cache Persistence
    """

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        cache_manager: Optional[CacheManager] = None
    ):
        self.client = client
        self.cache = cache_manager or CacheManager()
        self.stage1_resolver = CanonicalIdentityResolver(client=client)
        self.stage2_fetcher = Stage2DataFetcher(client=client)
        self.stage3_consensus = ConsensusAndScoringEngine()
        self.stage4_auditor = GroundingAuditor()

    async def execute(self, request: EnrichmentRequest) -> EnrichmentResponse:
        start_time = time.time()
        request_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        logger.info(f"Starting enrichment pipeline [ID: {request_id}] for input: {request}")

        # --- STEP 1: Fast Cache Check on raw input website if present ---
        if request.website and not request.force_refresh:
            approx_domain = self.stage1_resolver.clean_domain(request.website)
            if approx_domain:
                cached = self.cache.get(approx_domain)
                if cached:
                    logger.info(f"Cache HIT for domain '{approx_domain}'")
                    cached.metadata["request_id"] = request_id
                    cached.metadata["latency_ms"] = round((time.time() - start_time) * 1000, 1)
                    return cached

        # --- STEP 2: Stage 1 Canonical Identity Resolution ---
        stage1_res = await self.stage1_resolver.resolve(request)
        canonical_domain = stage1_res.canonical_domain

        # If entity is ambiguous (NEEDS_REVIEW), return candidate options immediately
        if stage1_res.disambiguation_required and stage1_res.candidates:
            logger.info(f"Disambiguation required for input '{request.name}': {len(stage1_res.candidates)} candidates")
            latency_ms = round((time.time() - start_time) * 1000, 1)
            return EnrichmentResponse(
                status="needs_disambiguation",
                match_decision=MatchDecision.NEEDS_REVIEW,
                request_id=request_id,
                canonical_domain=None,
                confidence_score=round(stage1_res.candidates[0].score, 3),
                confidence_tier=ConfidenceTier.DISAMBIGUATION_REQUIRED,
                data=CompanyData(
                    brand_name=request.name,
                    status=OperatingStatus.UNKNOWN,
                    active_web_presence=False
                ),
                provenance={},
                discrepancies=[],
                candidates=stage1_res.candidates,
                metadata={
                    "latency_ms": latency_ms,
                    "cache_hit": False,
                    "freshness_timestamp": now_iso
                }
            )

        # Check cache on resolved canonical domain
        if canonical_domain and not request.force_refresh:
            cached = self.cache.get(canonical_domain)
            if cached:
                logger.info(f"Cache HIT for canonical domain '{canonical_domain}'")
                cached.metadata["request_id"] = request_id
                cached.metadata["latency_ms"] = round((time.time() - start_time) * 1000, 1)
                return cached

        # --- STEP 3: Stage 2 Parallel Multi-Tier Data Fetching ---
        # Even if canonical_domain is None (no website or unresolved domain), we query official
        # registries by name / exact identifiers (CIK, LEI, registration_number, ticker).
        evidence_bag = await self.stage2_fetcher.fetch_all(
            canonical_domain=canonical_domain,
            brand_name=stage1_res.resolved_brand_name or request.name,
            jurisdiction_hint=request.jurisdiction_hint,
            cik=request.cik,
            lei=request.lei,
            registration_number=request.registration_number,
            ticker=request.ticker,
            exchange=request.exchange,
            http_status=stage1_res.http_status,
        )

        # If neither domain nor registry evidence could be resolved, abstain cleanly (UNRESOLVED)
        if not canonical_domain and not evidence_bag.extracted_evidence:
            latency_ms = round((time.time() - start_time) * 1000, 1)
            return EnrichmentResponse(
                status="unresolved",
                match_decision=MatchDecision.UNRESOLVED,
                request_id=request_id,
                canonical_domain=None,
                confidence_score=0.0,
                confidence_tier=ConfidenceTier.UNRESOLVED,
                data=CompanyData(
                    brand_name=request.name,
                    status=OperatingStatus.UNKNOWN,
                    active_web_presence=False
                ),
                provenance={},
                discrepancies=[],
                candidates=stage1_res.candidates,
                metadata={
                    "latency_ms": latency_ms,
                    "cache_hit": False,
                    "freshness_timestamp": now_iso,
                    "pages_crawled": 0,
                    "evidence_count": 0
                }
            )

        # --- STEP 4 & 5: Reconciliation, Grounding Audit & Post-Audit Calibrated Scoring ---
        data, provenance, discrepancies, score, tier = self.stage3_consensus.reconcile_all(
            evidence_bag=evidence_bag,
            active_web_presence=stage1_res.active_web_presence,
            candidates=stage1_res.candidates
        )

        # Explicit grounding audit check and score recalculation if any ungrounded quote was penalized
        audit_failures = GroundingAuditor.audit_provenance(
            data=data,
            provenance=provenance,
            raw_corpus=evidence_bag.full_text_corpus
        )
        if audit_failures:
            score, tier = self.stage3_consensus.compute_composite_score_and_tier(provenance)

        latency_ms = round((time.time() - start_time) * 1000, 1)

        # Determine final three-way MatchDecision
        if not provenance or score < 0.45:
            final_decision = MatchDecision.UNRESOLVED
            final_status = "unresolved"
            tier = ConfidenceTier.UNRESOLVED
        elif stage1_res.match_decision == MatchDecision.NEEDS_REVIEW:
            final_decision = MatchDecision.NEEDS_REVIEW
            final_status = "needs_disambiguation"
        else:
            final_decision = MatchDecision.AUTO_MATCH
            final_status = "success"

        response = EnrichmentResponse(
            status=final_status,
            match_decision=final_decision,
            request_id=request_id,
            canonical_domain=canonical_domain,
            confidence_score=score,
            confidence_tier=tier,
            data=data,
            provenance=provenance,
            discrepancies=discrepancies,
            candidates=stage1_res.candidates,
            metadata={
                "latency_ms": latency_ms,
                "cache_hit": False,
                "freshness_timestamp": now_iso,
                "pages_crawled": len(evidence_bag.pages),
                "evidence_count": len(evidence_bag.extracted_evidence),
                "grounding_audit_failures": audit_failures,
            }
        )

        # --- STEP 6: Cache Persistence ---
        cache_key = canonical_domain or (f"entity:{(request.cik or request.lei or request.name or '').lower()}")
        if cache_key and final_status == "success":
            self.cache.set(cache_key, response)

        logger.info(
            f"Enrichment pipeline complete for '{canonical_domain or request.name}' in {latency_ms}ms "
            f"[Decision: {final_decision.value}, Confidence: {score}, Tier: {tier.value}]"
        )
        return response
