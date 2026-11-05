import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints import agent, enrich, health
from app.config import get_settings
from app.core.exceptions import ServiceException
from app.core.middleware import CorrelationIdMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("company_service")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} in [{settings.ENVIRONMENT}] mode...")
    # Initialize shared connection-pooled HTTP client
    app.state.http_client = httpx.AsyncClient(
        headers={"User-Agent": settings.USER_AGENT},
        timeout=settings.HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=True
    )
    yield
    logger.info(f"Shutting down {settings.APP_NAME}...")
    if hasattr(app.state, "http_client") and app.state.http_client:
        await app.state.http_client.aclose()


app = FastAPI(
    title=settings.APP_NAME,
    description="Auditable, near-100% accurate company verification & autonomous enrichment agent.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Correlation ID & Timing Middleware
app.add_middleware(CorrelationIdMiddleware)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- UNIFIED ENTERPRISE ERROR HANDLERS ---
@app.exception_handler(ServiceException)
async def service_exception_handler(request: Request, exc: ServiceException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": exc.code,
            "message": exc.message,
            "details": exc.details
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "status": "error",
            "code": "VALIDATION_ERROR",
            "message": "Input validation failed. Please check the request payload.",
            "details": errors
        }
    )


# Register Endpoints
app.include_router(health.router, tags=["Health"])
app.include_router(enrich.router, prefix="/api/v1", tags=["Enrichment"])
app.include_router(agent.router, prefix="/api/v1", tags=["Autonomous Agent"])


@app.get("/")
async def root():
    return {
        "message": "Company Verification & Enrichment Agent API is running.",
        "docs": "/docs",
        "health": "/health"
    }
