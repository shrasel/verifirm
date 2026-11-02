import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.models.request import BatchEnrichmentRequest, EnrichmentRequest
from app.models.response import EnrichmentResponse
from app.pipeline.orchestrator import EnrichmentPipelineOrchestrator
from app.storage.cache import CacheManager

router = APIRouter()
orchestrator = EnrichmentPipelineOrchestrator()
cache = CacheManager()


@router.post(
    "/enrich",
    response_model=EnrichmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Enrich and verify a single company",
    description="Executes the full 4-stage auditable verification pipeline to enrich company data with near-100% accuracy."
)
async def enrich_company(request: EnrichmentRequest) -> EnrichmentResponse:
    try:
        return await orchestrator.execute(request)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Enrichment pipeline execution failed: {str(e)}"
        )


@router.post(
    "/enrich/batch",
    response_model=list[EnrichmentResponse],
    status_code=status.HTTP_200_OK,
    summary="Enrich a batch of companies concurrently",
    description="Executes parallel verification for up to 50 company records simultaneously."
)
async def enrich_batch(batch_request: BatchEnrichmentRequest) -> list[EnrichmentResponse]:
    tasks = [orchestrator.execute(req) for req in batch_request.items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    responses = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            req = batch_request.items[i]
            # Construct fallback error response
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
async def get_audit_trail(domain: str) -> EnrichmentResponse:
    cached = cache.get(domain)
    if not cached:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No cached audit record found for domain '{domain}'."
        )
    return cached

