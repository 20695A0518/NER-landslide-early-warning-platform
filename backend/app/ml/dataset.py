"""Training-set construction for the landslide susceptibility model.

IMPORTANT - read before quoting any accuracy number
---------------------------------------------------
With no public per-slope-unit inventory bundled, this module *synthesises* a
training set from a documented latent hazard process. Metrics reported by
`train.py` therefore measure how well the model recovers that process, NOT how
well it predicts real landslides in the North Eastern Region. They are a
pipeline correctness check and a demo enabler - nothing more.

Before any operational use, retrain on a real inventory:

    python -m app.ml.train --data path/to/inventory.csv

The CSV must carry the columns in `app.ml.features.FEATURE_ORDER` plus a binary
`label`. `app.services.inventory` has a helper that assembles exactly that from
the `historical_landslides` table once real records have been imported.

Why not just use the physics model as the label?
------------------------------------------------
Because then the ML component would be a redundant, lossy copy of a closed-form
equation. The latent process below deliberately contains effects the
infinite-slope model does not represent - a hill-cutting x rainfall
interaction, root-strength decay under shifting cultivation, toe erosion by
swollen streams, and observation noise - so the learned model contributes
information the physics term cannot, and the two disagree in informative ways.
"""

from __future__ import annotations

import math

import numpy as np

from app.data.ner_zones import NER_ZONES
from app.ml.features import (
    FEATURE_ORDER,
    LANDCOVER_STABILITY,
    LITHOLOGY_STRENGTH,
    build_feature_vector,
)
from app.ml.physics import (
    calibrate_suction_cohesion,
    factor_of_safety,
    fos_to_probability,
    seismic_amplification,
    wetness_index,
)

# Fraction of episodes drawn from monsoon rather than dry-season conditions.
MONSOON_SHARE = 0.62


def _perturb_zone(base: dict, rng: np.random.Generator) -> dict:
    """Jitter a seed zone into a plausible neighbouring slope unit.

    Real terrain varies continuously; training only on the 37 catalogued
    centroids would teach the model those 37 points rather than the underlying
    relationships.
    """
    zone = dict(base)
    zone["slope_deg"] = float(np.clip(base["slope_deg"] + rng.normal(0, 6.0), 5, 68))
    zone["elevation_m"] = float(max(30.0, base["elevation_m"] + rng.normal(0, 220.0)))
    zone["aspect_deg"] = float((base["aspect_deg"] + rng.normal(0, 45.0)) % 360.0)
    zone["curvature"] = float(rng.normal(0, 0.35))
    zone["soil_depth_m"] = float(np.clip(base["soil_depth_m"] + rng.normal(0, 0.7), 0.3, 6.0))
    zone["friction_angle_deg"] = float(
        np.clip(base["friction_angle_deg"] + rng.normal(0, 2.5), 16, 42)
    )
    zone["cohesion_kpa"] = float(np.clip(base["cohesion_kpa"] + rng.normal(0, 2.0), 0.5, 22))
    zone["ndvi"] = float(np.clip(base["ndvi"] + rng.normal(0, 0.09), 0.05, 0.92))
    zone["hill_cutting_index"] = float(
        np.clip(base["hill_cutting_index"] + rng.normal(0, 0.13), 0.0, 1.0)
    )
    zone["distance_to_road_m"] = float(
        max(5.0, base["distance_to_road_m"] * rng.lognormal(0, 0.55))
    )
    zone["distance_to_fault_m"] = float(
        max(50.0, base["distance_to_fault_m"] * rng.lognormal(0, 0.5))
    )
    zone["distance_to_stream_m"] = float(
        max(15.0, base["distance_to_stream_m"] * rng.lognormal(0, 0.5))
    )
    zone["annual_rainfall_mm"] = float(
        max(700.0, base["annual_rainfall_mm"] * rng.lognormal(0, 0.12))
    )
    zone["area_sq_km"] = base["area_sq_km"]
    zone["historical_event_count"] = int(max(0, rng.poisson(1.6)))

    # Apply the same back-analysis the seeder applies to real zones, so the
    # model never trains on parameter sets that describe an impossible slope.
    from app.ml.features import ROOT_COHESION_KPA

    zone["suction_cohesion_kpa"] = calibrate_suction_cohesion(
        slope_deg=zone["slope_deg"],
        soil_depth_m=zone["soil_depth_m"],
        friction_angle_deg=zone["friction_angle_deg"],
        cohesion_kpa=zone["cohesion_kpa"],
        root_cohesion_kpa=ROOT_COHESION_KPA.get(zone.get("land_cover", "forest"), 1.0),
        amplification=seismic_amplification(zone.get("seismic_zone", 5), zone["slope_deg"]),
    )
    return zone


def _sample_episode(zone: dict, rng: np.random.Generator) -> dict:
    """Draw one weather/sensor state for a zone.

    Rainfall is generated from a gamma process scaled by local climatology, so
    a Mawsynram episode and an Imphal episode are drawn from genuinely
    different distributions rather than a shared global one.
    """
    daily_normal = zone["annual_rainfall_mm"] / 365.0
    monsoon = rng.random() < MONSOON_SHARE

    if monsoon:
        # Heavy-tailed: most monsoon days are wet, a few are extreme.
        shape, scale = 1.15, daily_normal * 4.4
    else:
        shape, scale = 0.55, daily_normal * 0.85

    r24 = float(rng.gamma(shape, scale))
    # Antecedent totals must be internally consistent: 7d >= 72h >= 24h.
    r72 = r24 + float(rng.gamma(shape * 1.5, scale * 0.85))
    r7d = r72 + float(rng.gamma(shape * 2.2, scale * 0.75))

    # Peak hourly intensity: a burst fraction of the daily total.
    burst = float(np.clip(rng.beta(2.0, 5.0), 0.05, 0.75))
    intensity = r24 * burst

    depth = zone["soil_depth_m"]
    from app.ml.features import SOIL_DRAINAGE

    drainage = SOIL_DRAINAGE.get(zone.get("soil_type", "sandy_loam"), 1.0)
    wetness = wetness_index(r24, r72, r7d, depth, None, drainage)

    # Sensors exist on only some units, and report with error.
    has_sensor = rng.random() < 0.55
    if has_sensor:
        soil_moisture = float(np.clip(12.0 + wetness * 30.0 + rng.normal(0, 2.4), 6, 48))
        pore_pressure = float(max(0.0, wetness * 9.81 * depth * 0.8 + rng.normal(0, 1.4)))
        tilt = float(max(0.0, rng.gamma(0.6, 0.12) + wetness * 0.35))
    else:
        soil_moisture = None
        pore_pressure = 0.0
        tilt = 0.0

    return {
        "rainfall_24h_mm": r24,
        "rainfall_72h_mm": r72,
        "rainfall_7d_mm": r7d,
        "max_intensity_mm_hr": intensity,
        "soil_moisture_pct": soil_moisture,
        "pore_pressure_kpa": pore_pressure,
        "tilt_deg": tilt,
        "_wetness": wetness,
        "_monsoon": monsoon,
    }


def _latent_failure_probability(zone: dict, episode: dict, rng: np.random.Generator) -> float:
    """The generating process the model has to recover.

    Physics supplies the backbone; the remaining terms are effects a
    limit-equilibrium model does not capture but that field studies in the NER
    consistently report.
    """
    from app.ml.features import ROOT_COHESION_KPA

    wetness = episode["_wetness"]
    fos = factor_of_safety(
        slope_deg=zone["slope_deg"],
        soil_depth_m=zone["soil_depth_m"],
        cohesion_kpa=zone["cohesion_kpa"],
        friction_angle_deg=zone["friction_angle_deg"],
        wetness=wetness,
        root_cohesion_kpa=ROOT_COHESION_KPA.get(zone.get("land_cover", "forest"), 1.0),
        suction_cohesion_kpa=zone.get("suction_cohesion_kpa", 0.0),
    )
    logit = math.log(max(fos_to_probability(fos), 1e-6) / max(1 - fos_to_probability(fos), 1e-6))

    # 1. Road cutting removes lateral support - and the effect is strongly
    #    amplified when the slope is already wet (an interaction the
    #    infinite-slope model has no term for).
    cut_proximity = math.exp(-zone["distance_to_road_m"] / 220.0)
    logit += 2.6 * zone["hill_cutting_index"] * cut_proximity * (0.35 + wetness)

    # 2. Weak lithology and poor land cover.
    logit += 1.9 * (0.5 - LITHOLOGY_STRENGTH.get(zone.get("lithology", ""), 0.45))
    logit += 1.5 * (0.5 - LANDCOVER_STABILITY.get(zone.get("land_cover", ""), 0.5))

    # 3. Toe erosion: a swollen stream close to the slope foot.
    if zone["distance_to_stream_m"] < 250:
        logit += 0.9 * (episode["rainfall_72h_mm"] / 180.0)

    # 4. Damage zone around active faults, plus standing seismic conditioning.
    logit += 0.8 * math.exp(-zone["distance_to_fault_m"] / 1400.0)
    logit += 0.12 * (zone.get("seismic_zone", 5) - 4)

    # 5. Sites that have failed before tend to fail again.
    logit += 0.28 * min(zone.get("historical_event_count", 0), 6)

    # 6. Intense bursts do damage that daily totals hide.
    logit += 0.9 * math.log1p(episode["max_intensity_mm_hr"] / 12.0)

    # 7. Irreducible process noise - antecedent history, undetected structure.
    logit += rng.normal(0, 0.85)

    # Base rate: episodes producing a mapped failure are the exception. Tuned so
    # roughly one episode in eight yields a failure, which is the order of
    # magnitude reported for monsoon-season slope-unit inventories in the region.
    logit -= 4.05

    return 1.0 / (1.0 + math.exp(-logit))


def generate_training_frame(
    n_samples: int = 24000,
    seed: int = 20240915,
    label_noise: float = 0.03,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build (X, y, feature_names) for training.

    `label_noise` flips a small fraction of labels, standing in for the two
    ways real inventories are wrong: failures in uninhabited terrain that were
    never mapped, and scarps attributed to the wrong storm.
    """
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    labels: list[int] = []

    for _ in range(n_samples):
        base = NER_ZONES[int(rng.integers(len(NER_ZONES)))]
        zone = _perturb_zone(base, rng)
        episode = _sample_episode(zone, rng)

        p = _latent_failure_probability(zone, episode, rng)
        label = int(rng.random() < p)
        if rng.random() < label_noise:
            label = 1 - label

        rows.append(build_feature_vector(zone, episode))
        labels.append(label)

    return np.asarray(rows, dtype=np.float64), np.asarray(labels, dtype=np.int64), FEATURE_ORDER
