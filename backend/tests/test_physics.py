"""Tests for the process-based slope stability model.

These assert *physical behaviour*, not specific numbers, so the suite stays
meaningful if the coefficients are recalibrated. What must never change is the
direction of each relationship - a wetter slope cannot become more stable.
"""

import math

import pytest

from app.ml.physics import (
    calibrate_suction_cohesion,
    factor_of_safety,
    fos_to_probability,
    rainfall_threshold_ratio,
    seismic_amplification,
    wetness_index,
)


class TestWetnessIndex:
    def test_dry_slope_is_near_zero(self):
        assert wetness_index(0, 0, 0, 2.0) == pytest.approx(0.0, abs=0.01)

    def test_increases_monotonically_with_rainfall(self):
        values = [wetness_index(r, r * 1.5, r * 2, 2.0) for r in (0, 25, 50, 100, 200)]
        assert values == sorted(values)

    def test_is_bounded(self):
        assert wetness_index(5000, 8000, 12000, 1.0) <= 1.0
        assert wetness_index(-10, -10, -10, 1.0) >= 0.0

    def test_deeper_regolith_needs_more_rain(self):
        shallow = wetness_index(100, 150, 200, 0.5)
        deep = wetness_index(100, 150, 200, 4.0)
        assert shallow > deep

    def test_measured_moisture_overrides_rainfall_proxy(self):
        """A saturated sensor reading must raise wetness even with little rain."""
        without = wetness_index(5, 8, 12, 2.0, soil_moisture_pct=None)
        with_sensor = wetness_index(5, 8, 12, 2.0, soil_moisture_pct=42.0)
        assert with_sensor > without

    def test_free_draining_soil_wets_less(self):
        clay = wetness_index(120, 180, 220, 2.0, drainage_factor=0.55)
        gravel = wetness_index(120, 180, 220, 2.0, drainage_factor=1.5)
        assert clay > gravel


class TestFactorOfSafety:
    def test_saturation_reduces_stability(self):
        dry = factor_of_safety(35, 2.0, 10, 30, wetness=0.0)
        wet = factor_of_safety(35, 2.0, 10, 30, wetness=1.0)
        assert wet < dry

    def test_steeper_slope_is_less_stable(self):
        gentle = factor_of_safety(15, 2.0, 10, 30, wetness=0.5)
        steep = factor_of_safety(50, 2.0, 10, 30, wetness=0.5)
        assert steep < gentle

    def test_cohesion_and_friction_increase_stability(self):
        base = factor_of_safety(35, 2.0, 5, 25, wetness=0.5)
        assert factor_of_safety(35, 2.0, 20, 25, wetness=0.5) > base
        assert factor_of_safety(35, 2.0, 5, 40, wetness=0.5) > base

    def test_root_cohesion_helps(self):
        bare = factor_of_safety(35, 2.0, 5, 28, wetness=0.6, root_cohesion_kpa=0.0)
        forested = factor_of_safety(35, 2.0, 5, 28, wetness=0.6, root_cohesion_kpa=6.0)
        assert forested > bare

    def test_suction_decays_with_wetness(self):
        """Apparent cohesion must vanish as the column saturates.

        This is the property that gives the system dynamic range; if suction
        persisted when wet, steep slopes would look stable through a monsoon.
        """
        dry = factor_of_safety(40, 2.5, 8, 25, wetness=0.0, suction_cohesion_kpa=15)
        wet = factor_of_safety(40, 2.5, 8, 25, wetness=1.0, suction_cohesion_kpa=15)
        no_suction_wet = factor_of_safety(40, 2.5, 8, 25, wetness=1.0, suction_cohesion_kpa=0)
        assert dry > wet
        assert wet == pytest.approx(no_suction_wet, abs=1e-6)

    def test_output_is_clamped(self):
        assert 0.05 <= factor_of_safety(1, 0.2, 100, 45, 0.0) <= 5.0
        assert 0.05 <= factor_of_safety(74, 6.0, 0.1, 5, 1.0) <= 5.0


class TestCalibration:
    def test_recovers_strength_for_an_impossibly_steep_slope(self):
        """A 45-degree slope with weak parameters must be made dry-stable."""
        suction = calibrate_suction_cohesion(45, 3.0, 22, 5.0)
        assert suction > 0
        fos = factor_of_safety(45, 3.0, 5.0, 22, wetness=0.0, suction_cohesion_kpa=suction)
        assert fos == pytest.approx(1.25, abs=0.02)

    def test_does_not_inflate_an_already_stable_slope(self):
        assert calibrate_suction_cohesion(10, 1.0, 35, 20.0) == 0.0

    def test_calibrated_slope_still_fails_when_saturated(self):
        """Calibration must not make a steep slope permanently safe."""
        suction = calibrate_suction_cohesion(45, 3.0, 22, 5.0)
        wet = factor_of_safety(45, 3.0, 5.0, 22, wetness=1.0, suction_cohesion_kpa=suction)
        assert wet < 1.0


class TestRainfallThreshold:
    def test_no_rain_is_below_threshold(self):
        ratio, _ = rainfall_threshold_ratio(0, 0, 0, 2500)
        assert ratio == 0.0

    def test_normalises_to_local_climate(self):
        """The same rainfall must be less alarming where it rains far more.

        This is the property that stops the Khasi escarpment generating a
        permanent alarm for rainfall that is entirely ordinary there.
        """
        dry_region, _ = rainfall_threshold_ratio(150, 300, 20, annual_rainfall_mm=1500)
        wet_region, _ = rainfall_threshold_ratio(150, 300, 20, annual_rainfall_mm=11700)
        assert dry_region > wet_region

    def test_ratio_rises_with_intensity(self):
        low, _ = rainfall_threshold_ratio(20, 40, 3, 2500)
        high, _ = rainfall_threshold_ratio(200, 400, 40, 2500)
        assert high > low

    def test_reports_the_binding_duration(self):
        _, duration = rainfall_threshold_ratio(10, 20, 90, 2500)
        assert duration in (1, 24, 72)


class TestProbabilityMapping:
    def test_unity_maps_to_one_half(self):
        assert fos_to_probability(1.0) == pytest.approx(0.5)

    def test_is_monotonically_decreasing(self):
        values = [fos_to_probability(f) for f in (0.5, 0.8, 1.0, 1.4, 2.0)]
        assert values == sorted(values, reverse=True)

    def test_stays_in_range(self):
        assert 0.0 < fos_to_probability(5.0) < 1.0
        assert 0.0 < fos_to_probability(0.05) < 1.0


class TestSeismicAmplification:
    def test_higher_zone_amplifies_more(self):
        assert seismic_amplification(5, 30) > seismic_amplification(3, 30)

    def test_steep_ridges_amplify_more(self):
        assert seismic_amplification(5, 55) > seismic_amplification(5, 20)

    def test_unknown_zone_falls_back_safely(self):
        assert 1.0 <= seismic_amplification(99, 30) <= 1.2


def test_rainfall_threshold_uses_a_power_law():
    """A longer duration must have a lower critical intensity."""
    from app.ml.physics import ID_ALPHA, ID_BETA

    short = ID_ALPHA * (1 ** -ID_BETA)
    long = ID_ALPHA * (72 ** -ID_BETA)
    assert short > long
    assert math.isclose(long, ID_ALPHA / (72 ** ID_BETA))
