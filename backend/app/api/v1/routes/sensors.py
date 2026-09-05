"""Sensor network: station registry, telemetry ingest and health."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import Role
from app.models.sensor import SensorReading, SensorStation
from app.schemas.models import SensorReadingBatch, SensorReadingOut, SensorStationOut
from app.services import sensors as sensor_service

router = APIRouter(prefix="/sensors", tags=["sensors"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/stations", response_model=list[SensorStationOut])
def list_stations(db: DbSession, zone_id: int | None = None, status_filter: str | None = None):
    query = select(SensorStation)
    if zone_id:
        query = query.where(SensorStation.zone_id == zone_id)
    if status_filter:
        query = query.where(SensorStation.status == status_filter)
    return db.execute(query.order_by(SensorStation.code)).scalars().all()


@router.get("/health")
def network_health(db: DbSession):
    """Station availability - a warning system is only as good as its inputs."""
    stations = db.execute(select(SensorStation)).scalars().all()
    counts = {"online": 0, "degraded": 0, "offline": 0}
    low_battery = []

    for station in stations:
        counts[str(station.status)] = counts.get(str(station.status), 0) + 1
        if station.battery_pct < 30:
            low_battery.append(
                {
                    "code": station.code,
                    "name": station.name,
                    "battery_pct": station.battery_pct,
                    "status": station.status,
                    "last_seen_at": station.last_seen_at.isoformat()
                    if station.last_seen_at
                    else None,
                }
            )

    total = len(stations)
    return {
        "total_stations": total,
        "by_status": counts,
        "availability": round(counts["online"] / total, 3) if total else None,
        "needs_maintenance": sorted(low_battery, key=lambda s: s["battery_pct"]),
    }


@router.get("/readings", response_model=list[SensorReadingOut])
def list_readings(
    db: DbSession,
    station_id: int | None = None,
    zone_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    query = select(SensorReading)
    if station_id:
        query = query.where(SensorReading.station_id == station_id)
    if zone_id:
        query = query.join(
            SensorStation, SensorReading.station_id == SensorStation.id
        ).where(SensorStation.zone_id == zone_id)
    return db.execute(
        query.order_by(desc(SensorReading.recorded_at)).limit(limit)
    ).scalars().all()


@router.get("/zones/{zone_id}/state")
def zone_state(db: DbSession, zone_id: int):
    """Aggregated instrument state and anomaly reasoning for one zone."""
    state = sensor_service.zone_sensor_state(db, zone_id)
    score, reasons = sensor_service.anomaly_score(db, zone_id)
    return {
        "zone_id": zone_id,
        "state": state,
        "anomaly_score": score,
        "anomaly_reasons": reasons,
        "note": (
            "No instrumentation reporting for this zone - risk is computed from "
            "rainfall and terrain only."
        )
        if not state["has_data"]
        else None,
    }


@router.post("/readings", status_code=status.HTTP_202_ACCEPTED)
def ingest_readings(
    db: DbSession,
    payload: SensorReadingBatch,
    _user=Depends(require_roles(Role.ADMIN, Role.FIELD_OFFICER)),
):
    """Ingest telemetry from a field gateway.

    Batched because LoRaWAN / NB-IoT gateways buffer while the backhaul is down
    and then flush everything at once - which in these valleys is the normal
    case, not the exception. Unknown station codes are reported back rather
    than rejecting the whole batch, so one mis-provisioned node cannot discard
    good data from every other node on the same gateway.
    """
    stations = {s.code: s for s in db.execute(select(SensorStation)).scalars().all()}
    accepted, unknown = 0, []

    for reading in payload.readings:
        station = stations.get(reading.station_code)
        if station is None:
            unknown.append(reading.station_code)
            continue
        sensor_service.record_reading(db, station, reading.model_dump(exclude={"station_code"}))
        accepted += 1

    db.commit()
    return {
        "accepted": accepted,
        "rejected": len(unknown),
        "unknown_stations": sorted(set(unknown)),
    }


@router.post("/simulate", status_code=status.HTTP_202_ACCEPTED)
def simulate(db: DbSession, _user=Depends(require_roles(Role.ADMIN))):
    """Generate one synthetic reading per station (development aid)."""
    return sensor_service.simulate_round(db)
