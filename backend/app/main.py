"""PRAHARI - application entry point.

Predictive Real-time Alerting for Hazard Assessment in Regional Infrastructure:
an AI-assisted landslide early-warning platform for the eight North Eastern
states of India.

    uvicorn app.main:app --reload --port 8000

On first start the database is created and seeded, and the model artifact is
loaded if one has been trained. Both steps are safe to repeat.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.workers import scheduler

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("prahari")

# Third-party loggers are noisy at DEBUG and drown the risk-cycle output.
for noisy in ("apscheduler", "httpx", "httpcore", "multipart"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

BANNER = r"""
   ___  ___  ___   _  _   ___  ___  ___
  | _ \| _ \/   \ | || | / _ \| _ \|_ _|
  |  _/|   /| - | | __ || (_) |   / | |
  |_|  |_|_\|_|_| |_||_| \___/|_|_\|___|

  Landslide early warning for the North Eastern Region
"""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print(BANNER)
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)

    # Import models so every table is registered before create_all.
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        from app.services.seed import seed

        summary = seed(db)
        logger.info(
            "Seeded %s zones, %s roads, %s sensors",
            summary["zones"],
            summary["roads"],
            summary["sensors"],
        )
        if summary.get("users"):
            logger.info("Demo accounts created:")
            for account in summary["users"]:
                logger.info("   %-18s %-18s %s", account["username"], account["role"],
                            account["password"])

        # Score once at boot so the dashboard is never empty on first load.
        from app.services import risk_engine

        cycle = risk_engine.run_cycle(db, issue_alerts=True)
        logger.info("Initial risk cycle: %s", cycle["risk_distribution"])
    except Exception:
        logger.exception("Startup seeding failed - the API will still serve")
    finally:
        db.close()

    from app.ml.predictor import model_available

    if model_available():
        logger.info("ML model loaded")
    else:
        logger.warning(
            "No ML model artifact - running physics-only. "
            "Train one with: python -m app.ml.train"
        )

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        logger.info("Shutdown complete")


app = FastAPI(
    title="PRAHARI",
    description=(
        "AI-assisted landslide early warning and road-connectivity monitoring "
        "for the North Eastern Region of India."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace to a field device; always log the full one."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal error occurred. It has been logged.",
            "path": request.url.path,
        },
    )


app.include_router(api_router, prefix=settings.api_prefix)
app.mount("/media", StaticFiles(directory=str(settings.media_root)), name="media")


@app.get("/", tags=["system"])
def root():
    return {
        "name": "PRAHARI",
        "full_name": (
            "Predictive Real-time Alerting for Hazard Assessment in Regional Infrastructure"
        ),
        "version": "1.0.0",
        "description": "Landslide early warning for the North Eastern Region of India",
        "docs": "/docs",
        "api": settings.api_prefix,
    }


@app.get("/health", tags=["system"])
def health():
    """Liveness plus a component-by-component readiness view."""
    from sqlalchemy import text

    from app.ml.predictor import model_available
    from app.services import notifications
    from app.services import weather as weather_service

    db_ok = True
    db_error = None
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)
    finally:
        db.close()

    return {
        "status": "healthy" if db_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "database": {"ok": db_ok, "error": db_error},
            "ml_model": {"ok": model_available(), "mode": "hybrid"
                         if model_available() else "physics-only"},
            "weather": weather_service.provider_status(),
            "sms": notifications.provider_status(),
            "scheduler": {"jobs": scheduler.jobs()},
        },
    }
