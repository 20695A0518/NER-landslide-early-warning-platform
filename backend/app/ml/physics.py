"""Process-based slope stability, independent of the learned model.

Two classical formulations are implemented:

1. `factor_of_safety` - infinite-slope limit equilibrium with a perched water
   table. This is the standard first-order model for the shallow translational
   failures that dominate NER hillslopes (a 1-3 m regolith mantle sliding on
   bedrock during intense rain).

2. `rainfall_threshold_ratio` - a Caine-type intensity-duration power law,
   normalised by local mean annual precipitation. Normalisation matters
   enormously here: 200 mm in 24 h is an extreme event at Imphal (MAP 1500 mm)
   and an unremarkable one at Mawsynram (MAP ~11900 mm), and an un-normalised
   threshold would drown the dashboard in false alarms along the Khasi
   escarpment.

Keeping these separate from the ML model gives the platform a defensible answer
when the learned component is out of distribution - a new road cut, an
unprecedented rainfall total - and gives district officers a physical quantity
they can reason about rather than an opaque probability.
"""

from __future__ import annotations

import math

# Unit weights (kN/m3)
GAMMA_SAT = 19.0        # saturated soil
GAMMA_WATER = 9.81

# Reference mean annual precipitation for threshold normalisation (mm).
MAP_REFERENCE = 2500.0

# Caine-type power law I = ALPHA * D ** (-BETA), I in mm/h, D in hours.
# ALPHA is scaled up from global values because NER slopes are conditioned by,
# and locally adapted to, far wetter conditions than the global compilation.
ID_ALPHA = 22.0
ID_BETA = 0.52


def wetness_index(
    rainfall_24h_mm: float,
    rainfall_72h_mm: float,
    rainfall_7d_mm: float,
    soil_depth_m: float,
    soil_moisture_pct: float | None = None,
    drainage_factor: float = 1.0,
) -> float:
    """Fraction of the regolith column standing saturated, in [0, 1].

    A measured soil-moisture value, when a station reports one, is trusted more
    than the rainfall proxy - it integrates the antecedent history that the
    accumulation windows only approximate.
    """
    # Storage that has to be filled before a perched water table forms, in mm
    # per metre of regolith.
    #
    # This is the *drainable* porosity (specific yield, ~10-15%), not the total
    # porosity (~35-45%). The distinction is the whole model: most of the pore
    # volume is held against gravity at field capacity and never generates
    # positive pore pressure. Using total porosity would demand ~600 mm to wet
    # a 2 m column - more than most triggering storms deliver - and the system
    # would sit at "low" through the exact events it exists to catch.
    DRAINABLE_STORAGE_MM_PER_M = 120.0
    capacity_mm = max(soil_depth_m, 0.2) * DRAINABLE_STORAGE_MM_PER_M

    # Antecedent windows are weighted by how much each still contributes to
    # pore pressure now: recent rain dominates, older rain has partly drained.
    effective_mm = (
        rainfall_24h_mm * 1.0
        + max(rainfall_72h_mm - rainfall_24h_mm, 0.0) * 0.55
        + max(rainfall_7d_mm - rainfall_72h_mm, 0.0) * 0.20
    )
    rain_derived = effective_mm / (capacity_mm * max(drainage_factor, 0.3))

    if soil_moisture_pct is not None:
        # Map volumetric moisture onto saturation: ~12% is dry, ~42% saturated.
        sensor_derived = (soil_moisture_pct - 12.0) / 30.0
        combined = 0.45 * rain_derived + 0.55 * sensor_derived
    else:
        combined = rain_derived

    return max(0.0, min(1.0, combined))


# Rate at which apparent (suction) cohesion is lost as the column wets up.
# Exponent > 1 keeps most of the suction until the slope is genuinely wet, then
# sheds it quickly - the behaviour that makes a slope fail hours into a storm
# rather than at the first drop.
SUCTION_DECAY_EXPONENT = 1.5


def factor_of_safety(
    slope_deg: float,
    soil_depth_m: float,
    cohesion_kpa: float,
    friction_angle_deg: float,
    wetness: float,
    root_cohesion_kpa: float = 0.0,
    suction_cohesion_kpa: float = 0.0,
) -> float:
    """Infinite-slope factor of safety for a partially saturated column.

            c' + cr + cs(w) + (gamma_sat*z - gamma_w*h_w) * cos^2(b) * tan(phi')
    FS = ---------------------------------------------------------------------
                        gamma_sat * z * sin(b) * cos(b)

    `cs(w)` is apparent cohesion from matric suction, which decays to zero as
    the column saturates. Modelling it explicitly is what gives the system its
    dynamic range: a steep NER hillslope holds together all dry season on
    suction it simply does not have on day three of a monsoon depression.
    Folding that strength into a constant `c'` instead would make the steepest,
    most dangerous slopes look *insensitive* to rainfall.

    FS < 1 indicates the slope is theoretically unstable. Values are clamped to
    a sane display range rather than allowed to diverge on very gentle slopes,
    where the infinite-slope idealisation stops being meaningful.
    """
    beta = math.radians(max(min(slope_deg, 75.0), 1.0))
    phi = math.radians(max(min(friction_angle_deg, 45.0), 5.0))
    z = max(soil_depth_m, 0.2)
    w = max(0.0, min(wetness, 1.0))
    h_w = w * z

    driving = GAMMA_SAT * z * math.sin(beta) * math.cos(beta)
    if driving <= 1e-6:
        return 5.0

    suction = suction_cohesion_kpa * (1.0 - w) ** SUCTION_DECAY_EXPONENT
    effective_normal = (GAMMA_SAT * z - GAMMA_WATER * h_w) * math.cos(beta) ** 2
    resisting = (
        cohesion_kpa + root_cohesion_kpa + suction + max(effective_normal, 0.0) * math.tan(phi)
    )

    return max(0.05, min(resisting / driving, 5.0))


def fos_to_probability(fos: float, steepness: float = 4.0) -> float:
    """Map a factor of safety onto a failure probability.

    A logistic centred on FS = 1.0: the model is confident about FS well below
    or well above unity and appropriately uncertain near it, which is where
    parameter error actually lives.
    """
    return 1.0 / (1.0 + math.exp(steepness * (fos - 1.0)))


def rainfall_threshold_ratio(
    rainfall_24h_mm: float,
    rainfall_72h_mm: float,
    max_intensity_mm_hr: float,
    annual_rainfall_mm: float,
) -> tuple[float, int]:
    """Ratio of observed to critical rainfall intensity, and the binding duration.

    Evaluated across three durations; the worst ratio wins. A ratio >= 1.0 means
    the corridor has crossed its locally normalised triggering threshold.
    """
    climate_scale = max(annual_rainfall_mm, 600.0) / MAP_REFERENCE

    observations = [
        (1, max_intensity_mm_hr),
        (24, rainfall_24h_mm / 24.0),
        (72, rainfall_72h_mm / 72.0),
    ]

    worst_ratio, worst_duration = 0.0, 24
    for duration_h, intensity in observations:
        critical = ID_ALPHA * (duration_h ** -ID_BETA) * climate_scale
        if critical <= 0:
            continue
        ratio = intensity / critical
        if ratio > worst_ratio:
            worst_ratio, worst_duration = ratio, duration_h

    return round(worst_ratio, 4), worst_duration


def calibrate_suction_cohesion(
    slope_deg: float,
    soil_depth_m: float,
    friction_angle_deg: float,
    cohesion_kpa: float,
    root_cohesion_kpa: float = 0.0,
    amplification: float = 1.0,
    target_dry_fos: float = 1.25,
) -> float:
    """Recover the apparent cohesion implied by the slope still standing.

    Back-analysis. A slope that has survived previous dry seasons demonstrably
    has FS > 1 when drained, so a parameter set computing FS < 1 for it is
    wrong: the shear-strength estimate is too low, not the slope too steep.
    Regional susceptibility studies routinely invert this constraint, because
    cohesion carries by far the largest mapping uncertainty.

    The recovered increment is returned as *suction* cohesion, not added to
    `c'`, because the strength holding up a steep regolith slope in fine
    weather is overwhelmingly matric suction - and suction is exactly what a
    monsoon destroys. Booking it as permanent cohesion would produce a slope
    that is stable when dry and still stable when saturated, which is the
    opposite of the behaviour being modelled.

    Returns 0.0 when the authored parameters already satisfy the constraint, so
    a surveyed value is never inflated.
    """
    beta = math.radians(max(min(slope_deg, 75.0), 1.0))
    phi = math.radians(max(min(friction_angle_deg, 45.0), 5.0))
    z = max(soil_depth_m, 0.2)

    driving = GAMMA_SAT * z * math.sin(beta) * math.cos(beta)
    frictional_dry = GAMMA_SAT * z * math.cos(beta) ** 2 * math.tan(phi)

    required = (
        target_dry_fos * amplification * driving
        - frictional_dry
        - root_cohesion_kpa
        - cohesion_kpa
    )
    return round(max(required, 0.0), 2)


def seismic_amplification(seismic_zone: int, slope_deg: float) -> float:
    """Multiplier on driving stress from ambient seismicity.

    The whole NER sits in IS-1893 zone V (the highest), and ridge-top
    topographic amplification is well documented, so steep units in zone V carry
    a standing penalty even with no earthquake in progress.
    """
    base = {3: 1.00, 4: 1.04, 5: 1.09}.get(int(seismic_zone), 1.02)
    topographic = 1.0 + max(0.0, (slope_deg - 30.0)) / 200.0
    return base * topographic
