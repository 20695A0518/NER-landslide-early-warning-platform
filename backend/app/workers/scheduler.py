"""Background scheduling for the monitoring loop.

Two jobs run on independent intervals: weather polling (cheap, frequent) and
the full risk cycle (heavier, also issues alerts). They are separate because
the polling interval is set by what the upstream provider tolerates, while the
risk interval is set by how fast a slope can change - conflating them means
either hammering the API or scoring stale rainfall.

`max_instances=1` with `coalesce=True` matters: if a cycle runs long, the next
firing must be dropped, not queued behind it. A backlog of risk cycles would
issue alerts for conditions that have already passed.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import risk_engine
from app.services import weather as weather_service

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _poll_weather() -> None:
    db = SessionLocal()
    try:
        summary = await weather_service.refresh_all_zones(db)
        logger.info("Weather poll: %s", summary)
    except Exception:
        logger.exception("Weather poll failed")
    finally:
        db.close()


async def _run_risk_cycle() -> None:
    db = SessionLocal()
    try:
        summary = risk_engine.run_cycle(db)
        logger.info(
            "Risk cycle: %d zones, %d alerts",
            summary["zones_assessed"],
            len(summary["alerts_issued"]),
        )
    except Exception:
        logger.exception("Risk cycle failed")
    finally:
        db.close()


def start() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        _poll_weather,
        IntervalTrigger(minutes=settings.weather_poll_minutes),
        id="weather_poll",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_risk_cycle,
        IntervalTrigger(minutes=settings.risk_cycle_minutes),
        id="risk_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started - weather every %d min, risk cycle every %d min",
        settings.weather_poll_minutes,
        settings.risk_cycle_minutes,
    )
    return scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def jobs() -> list[dict]:
    if _scheduler is None:
        return []
    return [
        {
            "id": job.id,
            "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in _scheduler.get_jobs()
    ]
