import asyncio
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_cache_manager, get_concurrency_semaphore, get_http_client
from app.core.exceptions import EntityNotFoundException, ServiceException, ValidationException
from app.models.request import BatchEnrichmentRequest, EnrichmentRequest
from app.models.response import EnrichmentResponse
from app.pipeline.orchestrator import EnrichmentPipelineOrchestrator
from app.storage.cache import CacheManager

router = APIRouter()


@router.post(
    "/enrich",
    response_model=EnrichmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Enrich and verify a single company",
    description="Executes the 4-stage auditable verification pipeline with 3-way probabilistic entity resolution (AUTO_MATCH, NEEDS_REVIEW, UNRESOLVED)."
)
async def enrich_company(
    request: EnrichmentRequest,
    client: httpx.AsyncClient = Depends(get_http_client),
    cache: CacheManager = Depends(get_cache_manager),
    semaphore: asyncio.Semaphore = Depends(get_concurrency_semaphore)
) -> EnrichmentResponse:
    async with semaphore:
        orchestrator = EnrichmentPipelineOrchestrator(client=client)
        orchestrator.cache = cache
        try:
            return await orchestrator.execute(request)
        except ValueError as e:
            raise ValidationException(str(e))
        except Exception as e:
            raise ServiceException(
                message=f"Enrichment pipeline execution failed: {str(e)}",
                code="PIPELINE_ERROR"
            )


@router.post(
    "/enrich/batch",
    response_model=list[EnrichmentResponse],
    status_code=status.HTTP_200_OK,
    summary="Enrich a batch of companies concurrently",
    description="Executes parallel verification for up to 50 company records with concurrency throttling."
)
async def enrich_batch(
    batch_request: BatchEnrichmentRequest,
    client: httpx.AsyncClient = Depends(get_http_client),
    cache: CacheManager = Depends(get_cache_manager),
    semaphore: asyncio.Semaphore = Depends(get_concurrency_semaphore)
) -> list[EnrichmentResponse]:
    orchestrator = EnrichmentPipelineOrchestrator(client=client)
    orchestrator.cache = cache

    async def _throttled_run(req: EnrichmentRequest):
        async with semaphore:
            return await orchestrator.execute(req)

    tasks = [_throttled_run(req) for req in batch_request.items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    responses = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            req = batch_request.items[i]
            from app.models.company import CompanyData, OperatingStatus
            from app.models.response import ConfidenceTier
            responses.append(
                EnrichmentResponse(
                    status="error",
                    request_id=f"err-{i}",
                    canonical_domain=req.website,
                    confidence_score=0.0,
                    confidence_tier=ConfidenceTier.LOW_ASSURANCE,
                    data=CompanyData(brand_name=req.name, status=OperatingStatus.UNKNOWN),
                    provenance={},
                    discrepancies=[],
                    metadata={"error": str(res)}
                )
            )
        else:
            responses.append(res)

    return responses


@router.get(
    "/enrich/{domain}/audit",
    response_model=EnrichmentResponse,
    summary="Retrieve auditable provenance snapshot for a domain",
    description="Fetches the full verification envelope and source audit trail for a domain from cache."
)
async def get_audit_trail(
    domain: str,
    cache: CacheManager = Depends(get_cache_manager)
) -> EnrichmentResponse:
    cached = cache.get(domain)
    if not cached:
        raise EntityNotFoundException(
            message=f"No cached audit record found for domain '{domain}'.",
            details=[{"domain": domain}]
        )
    return cached
