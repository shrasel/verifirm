import logging
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("company_service.access")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Ensures every inbound request carries a unique Correlation ID (X-Correlation-ID)
    for end-to-end distributed tracing across logs, agent actions, and responses.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        start_time = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start_time) * 1000, 2)

        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        logger.info(
            f"[{correlation_id}] {request.method} {request.url.path} "
            f"-> status={response.status_code} in {duration_ms}ms"
        )
        return response
