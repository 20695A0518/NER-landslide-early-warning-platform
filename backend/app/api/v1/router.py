"""Mounts every v1 route module under a single router."""

from fastapi import APIRouter

from app.api.v1.routes import (
    alerts,
    auth,
    dashboard,
    reports,
    roads,
    sensors,
    sync,
    weather,
    zones,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(zones.router)
api_router.include_router(weather.router)
api_router.include_router(sensors.router)
api_router.include_router(reports.router)
api_router.include_router(alerts.router)
api_router.include_router(roads.router)
api_router.include_router(dashboard.router)
api_router.include_router(sync.router)
