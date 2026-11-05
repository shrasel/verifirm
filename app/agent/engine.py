import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
import httpx

from app.agent.context import AgentTrajectory
from app.agent.tools import AgentTools
from app.fetchers.base import RawEvidenceBag
from app.models.company import CompanyData, OperatingStatus
from app.models.request import EnrichmentRequest
from app.models.response import ConfidenceTier, EnrichmentResponse
from app.storage.cache import CacheManager

logger = logging.getLogger(__name__)


class AgentTrajectoryStore:
    """In-memory store for recent agent reasoning trajectories (up to 1,000 entries)."""
    _trajectories: dict[str, AgentTrajectory] = {}

    @classmethod
    def save(cls, trajectory: AgentTrajectory):
        if len(cls._trajectories) >= 1000:
            # Drop oldest entry
            oldest_key = next(iter(cls._trajectories))
            cls._trajectories.pop(oldest_key, None)
        cls._trajectories[trajectory.task_id] = trajectory

    @classmethod
    def get(cls, task_id: str) -> Optional[AgentTrajectory]:
        return cls._trajectories.get(task_id)


class CompanyEnrichmentAgent:
    """
    Autonomous Company Verification & Enrichment Agent.
    Operates via an auditable ReAct cycle (Reason -> Act -> Observe -> Reflect):
    1. Identity Canonicalization & TLD Resolution
    2. First-Party Ground-Truth Crawling
    3. Schema.org JSON-LD Structured Parsing
    4. Government Registry (SEC EDGAR) Cross-Verification
    5. Authority Matrix Consensus & Discrepancy Resolution
    6. Grounding Audit & Hallucination Elimination
    7. Self-Reflection & Final Audit Envelope Packaging
    """

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        cache_manager: Optional[CacheManager] = None
    ):
        self.client = client
        self.tools = AgentTools(client=client)
        self.cache = cache_manager or CacheManager()

    async def run(self, request: EnrichmentRequest) -> tuple[EnrichmentResponse, AgentTrajectory]:
        start_time = time.time()
        task_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        target_label = request.website or request.name or "unknown"
        trajectory = AgentTrajectory(
            task_id=task_id,
            target_company=target_label,
            goal="Verify and enrich company firmographics targeting >= 0.95 confidence with auditable consensus."
        )

        logger.info(f"Agent [{task_id}] dispatched for target: {target_label}")

        # --- CACHE CHECK ---
        if request.website and not request.force_refresh:
            approx_domain = self.tools.canonical_resolver.clean_domain(request.website)
            if approx_domain:
                cached = self.cache.get(approx_domain)
                if cached:
                    t_step = time.time()
                    trajectory.add_step(
                        thought=f"Checking primary cache for domain '{approx_domain}'.",
                        tool_name="cache_lookup",
                        tool_input={"domain": approx_domain},
                        observation_summary=f"Cache HIT. Found verified record with confidence {cached.confidence_score}.",
                        duration_ms=(time.time() - t_step) * 1000
                    )
                    trajectory.status = "completed"
                    trajectory.final_confidence_score = cached.confidence_score
                    trajectory.total_duration_ms = round((time.time() - start_time) * 1000, 2)
                    trajectory.completed_at = datetime.now(timezone.utc).isoformat()
                    AgentTrajectoryStore.save(trajectory)
                    return cached, trajectory

        # --- STEP 1: IDENTITY RESOLUTION & CANONICALIZATION ---
        t_step = time.time()
        thought_1 = (
            f"Investigating canonical identity for input '{target_label}'. "
            "Tracing HTTP redirects, stripping subdomains, and verifying active domain status."
        )
        stage1_res = await self.tools.resolve_identity(
            name=request.name,
            website=request.website,
            jurisdiction_hint=request.jurisdiction_hint
        )
        canonical_domain = stage1_res.canonical_domain

        trajectory.add_step(
            thought=thought_1,
            tool_name="resolve_identity",
            tool_input={"name": request.name, "website": request.website},
            observation_summary=(
                f"Resolved canonical domain: '{canonical_domain}' "
                f"(Active web presence: {stage1_res.active_web_presence}, "
                f"Candidates: {len(stage1_res.candidates)})."
            ),
            duration_ms=(time.time() - t_step) * 1000
        )

        # Handle homonyms / ambiguity
        if stage1_res.disambiguation_required and stage1_res.candidates:
            trajectory.add_step(
                thought=(
                    f"Target name '{request.name}' matched multiple distinct business entities. "
                    "Halting execution to request user disambiguation rather than guessing."
                ),
                tool_name="flag_disambiguation",
                tool_input={"candidates_count": len(stage1_res.candidates)},
                observation_summary=f"Flagged {len(stage1_res.candidates)} candidates for human review."
            )
            trajectory.status = "needs_disambiguation"
            trajectory.final_confidence_score = 0.50
            trajectory.total_duration_ms = round((time.time() - start_time) * 1000, 2)
            trajectory.completed_at = datetime.now(timezone.utc).isoformat()
            AgentTrajectoryStore.save(trajectory)

            response = EnrichmentResponse(
                status="needs_disambiguation",
                request_id=task_id,
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
                    "task_id": task_id,
                    "latency_ms": trajectory.total_duration_ms,
                    "cache_hit": False,
                    "freshness_timestamp": now_iso
                }
            )
            return response, trajectory

        if not canonical_domain:
            raise ValueError(f"Could not resolve canonical domain for request: {request}")

        # --- STEP 2: FIRST-PARTY GROUND-TRUTH CRAWL ---
        t_step = time.time()
        thought_2 = (
            f"Dispatching crawler to harvest first-party ground-truth evidence directly from {canonical_domain} "
            "across /, /about, /terms, /privacy, and /contact."
        )
        evidence_bag = RawEvidenceBag(
            canonical_domain=canonical_domain,
            brand_name=stage1_res.resolved_brand_name or request.name
        )
        evidence_bag = await self.tools.crawl_first_party_web(
            canonical_domain=canonical_domain,
            brand_name=evidence_bag.brand_name,
            evidence_bag=evidence_bag
        )
        trajectory.add_step(
            thought=thought_2,
            tool_name="crawl_first_party_web",
            tool_input={"canonical_domain": canonical_domain},
            observation_summary=(
                f"Crawled {len(evidence_bag.pages)} pages. "
                f"Discovered {len(evidence_bag.extracted_evidence)} raw evidence claims."
            ),
            duration_ms=(time.time() - t_step) * 1000
        )

        # --- STEP 3: SCHEMA.ORG JSON-LD PARSING ---
        t_step = time.time()
        thought_3 = (
            "Parsing embedded Schema.org JSON-LD metadata for structured Corporation / Organization "
            "attributes including registered legal name, headquarters, telephone, and email."
        )
        jsonld_claims_count = self.tools.extract_structured_jsonld(evidence_bag)
        trajectory.add_step(
            thought=thought_3,
            tool_name="extract_structured_jsonld",
            tool_input={"pages_evaluated": len(evidence_bag.pages)},
            observation_summary=f"Parsed JSON-LD graphs. Total extracted claims now {len(evidence_bag.extracted_evidence)}.",
            duration_ms=(time.time() - t_step) * 1000
        )

        # --- STEP 4: GOVERNMENT REGISTRY CROSS-VERIFICATION ---
        t_step = time.time()
        thought_4 = (
            f"Querying US SEC EDGAR filings for official corporate records matching '{canonical_domain}' "
            "to establish certified legal ground truth."
        )
        evidence_bag = await self.tools.query_government_registry(
            canonical_domain=canonical_domain,
            brand_name=evidence_bag.brand_name,
            evidence_bag=evidence_bag
        )
        trajectory.add_step(
            thought=thought_4,
            tool_name="query_government_registry",
            tool_input={"query": evidence_bag.brand_name or canonical_domain},
            observation_summary=(
                f"SEC EDGAR search complete. Discovered {len(evidence_bag.registry_filings)} official filings."
            ),
            duration_ms=(time.time() - t_step) * 1000
        )

        # --- STEP 5: CONSENSUS & AUTHORITY MATRIX RESOLUTION ---
        t_step = time.time()
        thought_5 = (
            "Reconciling all collected evidence using the Authority Matrix. "
            "Resolving discrepancies with priority: Government Registry > Terms of Service > Commercial."
        )
        data, provenance, discrepancies, score, tier = self.tools.reconcile_consensus(
            evidence_bag=evidence_bag,
            active_web_presence=stage1_res.active_web_presence
        )
        trajectory.add_step(
            thought=thought_5,
            tool_name="reconcile_consensus",
            tool_input={"total_claims": len(evidence_bag.extracted_evidence)},
            observation_summary=(
                f"Initial consensus score: {score} ({tier.value}). "
                f"Resolved {len(discrepancies)} conflicting field discrepancies."
            ),
            duration_ms=(time.time() - t_step) * 1000
        )

        # --- STEP 6: GROUNDING AUDIT (HALLUCINATION ELIMINATION) ---
        t_step = time.time()
        thought_6 = (
            "Auditing all first-party quotes and assertions against the scraped ground-truth corpus "
            "to guarantee zero hallucination."
        )
        grounding_failures = self.tools.audit_grounding(evidence_bag, provenance)
        trajectory.add_step(
            thought=thought_6,
            tool_name="audit_grounding",
            tool_input={"fields_audited": len(provenance)},
            observation_summary=(
                f"Grounding audit finished. {len(grounding_failures)} ungrounded fields penalized."
                if grounding_failures else "All citation quotes verified verbatim in scraped text."
            ),
            duration_ms=(time.time() - t_step) * 1000
        )

        # Recalculate composite score if grounding audit penalized any fields
        if grounding_failures:
            _, _, _, score, tier = self.tools.reconcile_consensus(
                evidence_bag=evidence_bag,
                active_web_presence=stage1_res.active_web_presence
            )

        # --- STEP 7: SYNTHESIS & AUDIT ENVELOPE PACKAGING ---
        total_duration = round((time.time() - start_time) * 1000, 2)
        trajectory.status = "completed"
        trajectory.final_confidence_score = score
        trajectory.total_duration_ms = total_duration
        trajectory.completed_at = datetime.now(timezone.utc).isoformat()

        trajectory.add_step(
            thought=(
                f"Verification workflow complete with assurance tier {tier.value} ({score}). "
                "Persisting audit envelope to cache and returning response."
            ),
            tool_name="finalize_envelope",
            tool_input={"final_confidence": score, "tier": tier.value},
            observation_summary="Final verified payload assembled successfully."
        )

        AgentTrajectoryStore.save(trajectory)

        response = EnrichmentResponse(
            status="success",
            request_id=task_id,
            canonical_domain=canonical_domain,
            confidence_score=score,
            confidence_tier=tier,
            data=data,
            provenance=provenance,
            discrepancies=discrepancies,
            candidates=stage1_res.candidates,
            metadata={
                "task_id": task_id,
                "latency_ms": total_duration,
                "cache_hit": False,
                "freshness_timestamp": now_iso,
                "pages_crawled": len(evidence_bag.pages),
                "evidence_count": len(evidence_bag.extracted_evidence),
                "agent_steps": len(trajectory.steps)
            }
        )

        # Persist to cache
        self.cache.set(canonical_domain, response)

        logger.info(
            f"Agent [{task_id}] completed enrichment for '{canonical_domain}' in {total_duration}ms "
            f"[Confidence: {score}, Tier: {tier.value}, Steps: {len(trajectory.steps)}]"
        )
        return response, trajectory
