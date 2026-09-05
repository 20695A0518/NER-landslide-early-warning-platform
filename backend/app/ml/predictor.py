"""Hybrid inference: learned model + slope physics + live field evidence.

The composite score is deliberately not a single model output. Three
independent lines of evidence are fused:

  learned      HistGradientBoosting over 30 terrain and trigger features
  physical     infinite-slope factor of safety and a normalised ID threshold
  observed     sensor anomalies and verified citizen/field reports

The first two are combined as a weighted opinion pool; the third can only ever
*raise* the score, through a noisy-OR style escalation. That asymmetry is
intentional. A cracked road surface reported by a patwari is strong positive
evidence that something is moving; the absence of a report is very weak
negative evidence, because most NER slopes have nobody standing on them. Letting
observations cut the score would systematically under-warn exactly the remote
villages this platform exists to protect.

If the model artifact is missing, `predict` degrades to physics-only rather
than failing - a monitoring system that stops scoring when a file is absent is
worse than one that scores conservatively.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.ml.features import (
    FEATURE_LABELS,
    FEATURE_ORDER,
    build_feature_vector,
)
from app.ml.physics import (
    factor_of_safety,
    fos_to_probability,
    rainfall_threshold_ratio,
    seismic_amplification,
    wetness_index,
)

logger = logging.getLogger(__name__)

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "model.joblib"

# Opinion-pool weights for the two predictive components.
W_LEARNED = 0.62
W_PHYSICAL = 0.38

_bundle: dict[str, Any] | None = None
_load_failed = False


def load_model(force: bool = False) -> dict[str, Any] | None:
    """Load and memoise the trained artifact. Returns None if unavailable."""
    global _bundle, _load_failed
    if force:
        _bundle, _load_failed = None, False
    if _bundle is not None or _load_failed:
        return _bundle

    if not MODEL_PATH.exists():
        logger.warning(
            "No model artifact at %s - running physics-only. "
            "Train one with: python -m app.ml.train",
            MODEL_PATH,
        )
        _load_failed = True
        return None

    try:
        bundle = joblib.load(MODEL_PATH)
    except Exception:  # pragma: no cover - corrupt artifact
        logger.exception("Failed to load model artifact; falling back to physics-only")
        _load_failed = True
        return None

    trained_order = bundle.get("feature_order")
    if trained_order != FEATURE_ORDER:
        # Refusing here is the whole point of pinning the order: silently
        # scoring a permuted vector would produce plausible, wrong numbers.
        logger.error(
            "Model artifact was trained on a different feature set (%d features) "
            "than this build expects (%d). Retrain with: python -m app.ml.train",
            len(trained_order or []),
            len(FEATURE_ORDER),
        )
        _load_failed = True
        return None

    _bundle = bundle
    logger.info("Loaded model %s", bundle.get("model_version"))
    return _bundle


def model_available() -> bool:
    return load_model() is not None


def _ml_probability(features: list[float]) -> float | None:
    bundle = load_model()
    if bundle is None:
        return None
    row = np.asarray([features], dtype=np.float64)
    return float(bundle["model"].predict_proba(row)[0, 1])


def _explain(zone: dict, dynamic: dict, fos: float, ratio: float, wetness: float) -> list[dict]:
    """Rank the drivers behind this specific score.

    Model-wide permutation importance answers "what matters in general"; a
    district officer at 2 a.m. needs "why is *this* slope lit up right now", so
    these contributions are computed per-zone from the physical terms.
    """
    factors: list[dict] = []

    def add(key: str, contribution: float, value: float, unit: str, note: str) -> None:
        if contribution < 0.02:
            return
        factors.append(
            {
                "factor": key,
                "label": FEATURE_LABELS.get(key, key.replace("_", " ").title()),
                "contribution": round(min(contribution, 1.0), 3),
                "value": round(value, 2),
                "unit": unit,
                "note": note,
            }
        )

    add(
        "factor_of_safety",
        max(0.0, (1.45 - fos) / 1.45),
        fos,
        "",
        "Below 1.0 the slope is theoretically unstable"
        if fos < 1.0
        else "Margin above failure is narrowing"
        if fos < 1.3
        else "Slope retains a stability margin",
    )
    add(
        "rain_threshold_ratio",
        min(ratio / 1.6, 1.0),
        ratio,
        "x threshold",
        "Locally normalised rainfall threshold exceeded"
        if ratio >= 1.0
        else "Approaching the local rainfall threshold",
    )
    add(
        "wetness_index",
        wetness,
        wetness * 100,
        "% saturated",
        "Regolith column is near saturation" if wetness > 0.7 else "Soil moisture is elevated",
    )
    add(
        "hill_cutting_index",
        zone.get("hill_cutting_index", 0.0) * math.exp(-zone.get("distance_to_road_m", 500) / 220),
        zone.get("hill_cutting_index", 0.0),
        "index",
        "Unsupported road cut close to the slope toe",
    )
    add(
        "slope_deg",
        max(0.0, (zone.get("slope_deg", 25) - 25) / 35),
        zone.get("slope_deg", 25),
        "deg",
        "Steep terrain",
    )
    add(
        "landcover_stability",
        max(0.0, 0.6 - zone.get("ndvi", 0.6)),
        zone.get("ndvi", 0.6),
        "NDVI",
        "Sparse vegetation offers little root reinforcement",
    )
    add(
        "max_intensity_mm_hr",
        min(dynamic.get("max_intensity_mm_hr", 0.0) / 45.0, 1.0),
        dynamic.get("max_intensity_mm_hr", 0.0),
        "mm/hr",
        "High-intensity rainfall burst",
    )
    add(
        "tilt_deg",
        min((dynamic.get("tilt_deg") or 0.0) / 1.2, 1.0),
        dynamic.get("tilt_deg") or 0.0,
        "deg",
        "Inclinometer reports active ground movement",
    )
    add(
        "historical_density",
        min(zone.get("historical_event_count", 0) / 5.0, 1.0),
        zone.get("historical_event_count", 0),
        "events",
        "Slope has failed here before",
    )

    factors.sort(key=lambda f: f["contribution"], reverse=True)
    return factors[:6]


def _narrative(level: str, factors: list[dict], ratio: float, fos: float, horizon: int) -> str:
    lead = {
        "critical": "Failure conditions are met.",
        "high": "Conditions are approaching failure.",
        "moderate": "Conditions warrant watching.",
        "low": "Slope is currently stable.",
    }[level]

    parts = [lead]
    if fos < 1.0:
        parts.append(f"Computed factor of safety is {fos:.2f}, below the stability limit of 1.0.")
    else:
        parts.append(f"Factor of safety is {fos:.2f}.")

    if ratio >= 1.0:
        parts.append(
            f"Rainfall has reached {ratio:.1f}x the locally normalised "
            f"{horizon}-hour triggering threshold."
        )
    elif ratio > 0.6:
        parts.append(f"Rainfall is at {ratio:.1f}x the {horizon}-hour triggering threshold.")

    drivers = [f["label"].lower() for f in factors[:3]]
    if drivers:
        parts.append("Principal drivers: " + ", ".join(drivers) + ".")

    return " ".join(parts)


def predict(
    zone: dict,
    dynamic: dict,
    sensor_anomaly: float = 0.0,
    field_report_score: float = 0.0,
) -> dict:
    """Score one zone.

    Args:
        zone: static terrain attributes (see `features.zone_to_dict`).
        dynamic: current rainfall and sensor state.
        sensor_anomaly: 0-1 summary of instrument anomalies in this zone.
        field_report_score: 0-1 summary of recent verified ground reports.

    Returns a dict ready to persist as a `RiskAssessment`.
    """
    features = build_feature_vector(zone, dynamic)

    from app.ml.features import ROOT_COHESION_KPA, SOIL_DRAINAGE

    drainage = SOIL_DRAINAGE.get(zone.get("soil_type", "sandy_loam"), 1.0)
    wetness = wetness_index(
        dynamic.get("rainfall_24h_mm", 0.0),
        dynamic.get("rainfall_72h_mm", 0.0),
        dynamic.get("rainfall_7d_mm", 0.0),
        zone.get("soil_depth_m", 2.0),
        dynamic.get("soil_moisture_pct"),
        drainage,
    )
    fos = factor_of_safety(
        slope_deg=zone.get("slope_deg", 25.0),
        soil_depth_m=zone.get("soil_depth_m", 2.0),
        cohesion_kpa=zone.get("cohesion_kpa", 8.0),
        friction_angle_deg=zone.get("friction_angle_deg", 30.0),
        wetness=wetness,
        root_cohesion_kpa=ROOT_COHESION_KPA.get(zone.get("land_cover", "forest"), 1.0),
        suction_cohesion_kpa=zone.get("suction_cohesion_kpa", 0.0),
    )
    # Ambient seismicity erodes the margin without any earthquake occurring.
    fos /= seismic_amplification(zone.get("seismic_zone", 5), zone.get("slope_deg", 25.0))

    ratio, horizon = rainfall_threshold_ratio(
        dynamic.get("rainfall_24h_mm", 0.0),
        dynamic.get("rainfall_72h_mm", 0.0),
        dynamic.get("max_intensity_mm_hr", 0.0),
        zone.get("annual_rainfall_mm", 2500.0),
    )

    physical_p = fos_to_probability(fos)
    # The threshold curve is an independent trigger signal; blend it into the
    # physical opinion rather than treating it as a separate vote.
    trigger_p = 1.0 / (1.0 + math.exp(-3.0 * (ratio - 1.0)))
    physical_p = 0.65 * physical_p + 0.35 * trigger_p

    ml_p = _ml_probability(features)
    if ml_p is None:
        base = physical_p
        confidence = 0.45          # physics-only: usable, but say so
        model_version = "physics-only"
    else:
        base = W_LEARNED * ml_p + W_PHYSICAL * physical_p
        # Confidence is highest when the two independent components agree.
        agreement = 1.0 - abs(ml_p - physical_p)
        confidence = round(0.55 + 0.4 * agreement, 3)
        bundle = load_model()
        model_version = (bundle or {}).get("model_version", "unknown")

    # Observed evidence escalates only.
    evidence = max(0.0, min(1.0, 0.55 * sensor_anomaly + 0.65 * field_report_score))
    probability = base + (1.0 - base) * evidence
    probability = float(max(0.0, min(probability, 0.995)))

    from app.models.enums import RiskLevel

    level = RiskLevel.from_probability(probability)
    factors = _explain(zone, dynamic, fos, ratio, wetness)

    # Lead time shortens as the slope gets closer to failing: a slope already
    # below FS 1.0 with the threshold crossed may go within hours.
    if probability >= 0.82:
        lead_time = 6
    elif probability >= 0.65:
        lead_time = 12
    elif probability >= 0.40:
        lead_time = 24
    else:
        lead_time = 48

    return {
        "probability": round(probability, 4),
        "risk_level": level.value,
        "confidence": float(confidence),
        "lead_time_hours": lead_time,
        "ml_probability": round(ml_p, 4) if ml_p is not None else 0.0,
        "factor_of_safety": round(fos, 3),
        "rainfall_threshold_ratio": round(ratio, 3),
        "sensor_anomaly_score": round(sensor_anomaly, 3),
        "field_report_score": round(field_report_score, 3),
        "contributing_factors": factors,
        "narrative": _narrative(level.value, factors, ratio, fos, horizon),
        "model_version": model_version,
        "trigger_forecast_horizon_h": horizon,
    }
