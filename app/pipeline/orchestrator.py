import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
import httpx

from app.consensus.confidence_scorer import ConsensusAndScoringEngine
from app.models.company import CompanyData, OperatingStatus
from app.models.request import EnrichmentRequest
from app.models.response import CandidateEntity, ConfidenceTier, EnrichmentResponse
from app.pipeline.stage1_canonical import CanonicalIdentityResolver
from app.pipeline.stage2_fetch import Stage2DataFetcher
from app.pipeline.stage4_grounding import GroundingAuditor
from app.storage.cache import CacheManager

logger = logging.getLogger(__name__)


class EnrichmentPipelineOrchestrator:
    """
    End-to-End Verification Pipeline Orchestrator:
    1. Cache Check
    2. Stage 1: Identity Resolution & Canonicalization
    3. Stage 2: Parallel Multi-Tier Data Fetching
    4. Stage 3: Reconciliation & Consensus Engine
    5. Stage 4: Grounded LLM / Character-Span Auditing
    6. Response Packaging & Cache Persistence
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client
        self.cache = CacheManager()
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

        # If entity is ambiguous, return disambiguation response immediately
        if stage1_res.disambiguation_required and stage1_res.candidates:
            logger.info(f"Disambiguation required for input '{request.name}': {len(stage1_res.candidates)} candidates")
            latency_ms = round((time.time() - start_time) * 1000, 1)
            return EnrichmentResponse(
                status="needs_disambiguation",
                request_id=request_id,
                canonical_domain=canonical_domain,
                confidence_score=0.50,
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

        if not canonical_domain:
            raise ValueError(f"Could not resolve canonical domain for request: {request}")

        # Check cache on resolved canonical domain
        if not request.force_refresh:
            cached = self.cache.get(canonical_domain)
            if cached:
                logger.info(f"Cache HIT for canonical domain '{canonical_domain}'")
                cached.metadata["request_id"] = request_id
                cached.metadata["latency_ms"] = round((time.time() - start_time) * 1000, 1)
                return cached

        # --- STEP 3: Stage 2 Parallel Multi-Tier Data Fetching ---
        evidence_bag = await self.stage2_fetcher.fetch_all(
            canonical_domain=canonical_domain,
            brand_name=stage1_res.resolved_brand_name or request.name
        )

        # --- STEP 4: Stage 3 Reconciliation & Consensus Engine ---
        data, provenance, discrepancies, score, tier = self.stage3_consensus.reconcile_all(
            evidence_bag=evidence_bag,
            active_web_presence=stage1_res.active_web_presence
        )

        # --- STEP 5: Stage 4 Grounding Audit (Hallucination Check) ---
        GroundingAuditor.audit_provenance(
            data=data,
            provenance=provenance,
            raw_corpus=evidence_bag.full_text_corpus
        )

        latency_ms = round((time.time() - start_time) * 1000, 1)

        response = EnrichmentResponse(
            status="success",
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
                "evidence_count": len(evidence_bag.extracted_evidence)
            }
        )

        # --- STEP 6: Cache Persistence ---
        self.cache.set(canonical_domain, response)

        logger.info(
            f"Enrichment pipeline complete for '{canonical_domain}' in {latency_ms}ms "
            f"[Confidence: {score}, Tier: {tier.value}]"
        )
        return response
