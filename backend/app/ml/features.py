"""Feature contract shared by training and inference.

`FEATURE_ORDER` is the single source of truth for the model's input layout.
Training and serving both build vectors through `build_feature_vector`, so a
feature added here cannot silently skew inference: the artifact records the
order it was trained with and `predictor` refuses to load a mismatched model.
"""

from __future__ import annotations

import math

from app.ml.physics import factor_of_safety, rainfall_threshold_ratio, wetness_index

# --- Ordinal encodings ------------------------------------------------------
# Categorical terrain descriptors are mapped to a scalar rather than one-hot
# encoded: the categories are genuinely ordered by shear strength / stability,
# the ordering is domain knowledge worth giving the model for free, and it keeps
# the vector short enough to train well on a small inventory.

LITHOLOGY_STRENGTH: dict[str, float] = {
    "shale_siltstone": 0.20,      # weakest - Surma/Barail belts, Mizoram + Manipur
    "sandstone_shale": 0.35,
    "alluvium_terrace": 0.40,
    "phyllite_schist": 0.45,
    "limestone_karst": 0.55,      # strong intact rock but solution-weakened
    "quartzite_sandstone": 0.70,
    "ophiolite_melange": 0.50,
    "granite_gneiss": 0.85,
    "gneiss_migmatite": 0.80,
}

LANDCOVER_STABILITY: dict[str, float] = {
    "mining_disturbed": 0.05,
    "urban_terraced": 0.15,
    "degraded_scrub": 0.25,
    "jhum_cultivation": 0.30,     # shifting cultivation - periodic root loss
    "grassland": 0.40,
    "agriculture": 0.45,
    "agriculture_terraced": 0.55,
    "orchard_terraced": 0.65,
    "alpine_scrub": 0.50,
    "forest": 0.90,               # root cohesion + interception
}

# Root cohesion contributed by land cover, in kPa. Forest roots add real
# strength to a shallow regolith; a quarry face adds none.
ROOT_COHESION_KPA: dict[str, float] = {
    "forest": 6.0,
    "orchard_terraced": 3.5,
    "agriculture_terraced": 2.0,
    "alpine_scrub": 2.0,
    "grassland": 1.5,
    "jhum_cultivation": 1.0,
    "agriculture": 1.0,
    "degraded_scrub": 0.5,
    "urban_terraced": 0.2,
    "mining_disturbed": 0.0,
}

# Relative drainage efficiency by soil texture; coarse soils shed water fast.
SOIL_DRAINAGE: dict[str, float] = {
    "gravelly_sandy_loam": 1.5,
    "sandy_loam": 1.3,
    "sandy_clay_loam": 1.0,
    "clay_loam": 0.8,
    "silty_clay_loam": 0.7,
    "clay": 0.55,
}

FEATURE_ORDER: list[str] = [
    # --- Static terrain conditioning ---
    "slope_deg",
    "slope_sin",
    "elevation_km",
    "aspect_sin",
    "aspect_cos",
    "curvature",
    "soil_depth_m",
    "friction_angle_deg",
    "cohesion_kpa",
    "lithology_strength",
    "landcover_stability",
    "soil_drainage",
    "ndvi",
    # --- Anthropogenic / setting ---
    "log_distance_to_road",
    "log_distance_to_fault",
    "log_distance_to_stream",
    "hill_cutting_index",
    "seismic_zone",
    "historical_density",
    # --- Dynamic trigger state ---
    "rainfall_24h_mm",
    "rainfall_72h_mm",
    "rainfall_7d_mm",
    "max_intensity_mm_hr",
    "rain_anomaly_ratio",
    "wetness_index",
    # --- Derived physics (given to the model as engineered features) ---
    "factor_of_safety",
    "rain_threshold_ratio",
    # --- In-situ instrumentation ---
    "soil_moisture_pct",
    "pore_pressure_kpa",
    "tilt_deg",
]

N_FEATURES = len(FEATURE_ORDER)

# Human-readable labels for the explainability panel on the dashboard.
FEATURE_LABELS: dict[str, str] = {
    "slope_deg": "Slope steepness",
    "slope_sin": "Slope driving component",
    "elevation_km": "Elevation",
    "aspect_sin": "Slope aspect (E-W)",
    "aspect_cos": "Slope aspect (N-S)",
    "curvature": "Slope curvature",
    "soil_depth_m": "Regolith depth",
    "friction_angle_deg": "Soil friction angle",
    "cohesion_kpa": "Soil cohesion",
    "lithology_strength": "Rock strength",
    "landcover_stability": "Land cover stability",
    "soil_drainage": "Soil drainage",
    "ndvi": "Vegetation cover (NDVI)",
    "log_distance_to_road": "Proximity to road cut",
    "log_distance_to_fault": "Proximity to fault",
    "log_distance_to_stream": "Proximity to stream (toe erosion)",
    "hill_cutting_index": "Unplanned hill cutting",
    "seismic_zone": "Seismic zone",
    "historical_density": "Past landslide density",
    "rainfall_24h_mm": "Rainfall last 24 h",
    "rainfall_72h_mm": "Rainfall last 72 h",
    "rainfall_7d_mm": "Rainfall last 7 days",
    "max_intensity_mm_hr": "Peak rainfall intensity",
    "rain_anomaly_ratio": "Rainfall vs seasonal normal",
    "wetness_index": "Slope saturation",
    "factor_of_safety": "Factor of safety",
    "rain_threshold_ratio": "Rainfall threshold exceedance",
    "soil_moisture_pct": "Measured soil moisture",
    "pore_pressure_kpa": "Measured pore pressure",
    "tilt_deg": "Measured ground tilt",
}


def _safe_log_distance(metres: float | None, default: float = 500.0) -> float:
    return math.log1p(max(float(metres if metres is not None else default), 0.0))


def build_feature_vector(zone: dict, dynamic: dict) -> list[float]:
    """Assemble one model input row.

    `zone` carries the static terrain attributes (a Zone row or the seed dict);
    `dynamic` carries the current rainfall / sensor state. Both are plain dicts
    so the same code path serves training rows and live inference.
    """
    slope = float(zone.get("slope_deg", 25.0))
    aspect = math.radians(float(zone.get("aspect_deg", 180.0)))
    soil_depth = float(zone.get("soil_depth_m", 2.0))
    land_cover = zone.get("land_cover", "forest")
    soil_type = zone.get("soil_type", "sandy_loam")
    annual_rain = float(zone.get("annual_rainfall_mm", 2500.0))

    r24 = float(dynamic.get("rainfall_24h_mm", 0.0))
    r72 = float(dynamic.get("rainfall_72h_mm", 0.0))
    r7d = float(dynamic.get("rainfall_7d_mm", 0.0))
    intensity = float(dynamic.get("max_intensity_mm_hr", 0.0))
    soil_moisture = dynamic.get("soil_moisture_pct")

    drainage = SOIL_DRAINAGE.get(soil_type, 1.0)
    wetness = wetness_index(r24, r72, r7d, soil_depth, soil_moisture, drainage)

    fos = factor_of_safety(
        slope_deg=slope,
        soil_depth_m=soil_depth,
        cohesion_kpa=float(zone.get("cohesion_kpa", 8.0)),
        friction_angle_deg=float(zone.get("friction_angle_deg", 30.0)),
        wetness=wetness,
        root_cohesion_kpa=ROOT_COHESION_KPA.get(land_cover, 1.0),
        suction_cohesion_kpa=float(zone.get("suction_cohesion_kpa", 0.0)),
    )
    threshold_ratio, _ = rainfall_threshold_ratio(r24, r72, intensity, annual_rain)

    # Daily rainfall against the seasonal normal for this locality.
    daily_normal = max(annual_rain / 365.0, 0.5)
    rain_anomaly = r24 / (daily_normal * 6.0)

    values: dict[str, float] = {
        "slope_deg": slope,
        "slope_sin": math.sin(math.radians(slope)),
        "elevation_km": float(zone.get("elevation_m", 500.0)) / 1000.0,
        "aspect_sin": math.sin(aspect),
        "aspect_cos": math.cos(aspect),
        "curvature": float(zone.get("curvature", 0.0)),
        "soil_depth_m": soil_depth,
        "friction_angle_deg": float(zone.get("friction_angle_deg", 30.0)),
        "cohesion_kpa": float(zone.get("cohesion_kpa", 8.0)),
        "lithology_strength": LITHOLOGY_STRENGTH.get(zone.get("lithology", ""), 0.45),
        "landcover_stability": LANDCOVER_STABILITY.get(land_cover, 0.5),
        "soil_drainage": drainage,
        "ndvi": float(zone.get("ndvi", 0.6)),
        "log_distance_to_road": _safe_log_distance(zone.get("distance_to_road_m")),
        "log_distance_to_fault": _safe_log_distance(zone.get("distance_to_fault_m"), 5000.0),
        "log_distance_to_stream": _safe_log_distance(zone.get("distance_to_stream_m"), 800.0),
        "hill_cutting_index": float(zone.get("hill_cutting_index", 0.3)),
        "seismic_zone": float(zone.get("seismic_zone", 5)),
        "historical_density": float(zone.get("historical_event_count", 0))
        / max(float(zone.get("area_sq_km", 1.0)), 0.5),
        "rainfall_24h_mm": r24,
        "rainfall_72h_mm": r72,
        "rainfall_7d_mm": r7d,
        "max_intensity_mm_hr": intensity,
        "rain_anomaly_ratio": rain_anomaly,
        "wetness_index": wetness,
        "factor_of_safety": fos,
        "rain_threshold_ratio": threshold_ratio,
        "soil_moisture_pct": float(soil_moisture) if soil_moisture is not None else 24.0,
        "pore_pressure_kpa": float(dynamic.get("pore_pressure_kpa") or 0.0),
        "tilt_deg": float(dynamic.get("tilt_deg") or 0.0),
    }

    return [values[name] for name in FEATURE_ORDER]


def zone_to_dict(zone) -> dict:
    """Project a Zone ORM row onto the plain dict the feature builder expects."""
    return {
        "slope_deg": zone.slope_deg,
        "aspect_deg": zone.aspect_deg,
        "elevation_m": zone.elevation_m,
        "curvature": zone.curvature,
        "soil_depth_m": zone.soil_depth_m,
        "friction_angle_deg": zone.friction_angle_deg,
        "cohesion_kpa": zone.cohesion_kpa,
        "suction_cohesion_kpa": zone.suction_cohesion_kpa,
        "lithology": zone.lithology,
        "soil_type": zone.soil_type,
        "land_cover": zone.land_cover,
        "ndvi": zone.ndvi,
        "annual_rainfall_mm": zone.annual_rainfall_mm,
        "distance_to_road_m": zone.distance_to_road_m,
        "distance_to_fault_m": zone.distance_to_fault_m,
        "distance_to_stream_m": zone.distance_to_stream_m,
        "hill_cutting_index": zone.hill_cutting_index,
        "seismic_zone": zone.seismic_zone,
        "historical_event_count": zone.historical_event_count,
        "area_sq_km": zone.area_sq_km,
    }
