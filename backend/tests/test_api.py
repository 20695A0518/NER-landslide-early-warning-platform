"""End-to-end API tests covering auth, reporting, offline sync and alerting."""

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/v1"


class TestAuth:
    def test_login_succeeds_with_seeded_credentials(self, client, seeded):
        response = client.post(
            f"{API}/auth/login", data={"username": "admin", "password": "admin123"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["role"] == "admin"
        assert body["access_token"]

    def test_wrong_password_is_rejected(self, client, seeded):
        response = client.post(
            f"{API}/auth/login", data={"username": "admin", "password": "wrong"}
        )
        assert response.status_code == 401

    def test_unknown_user_is_rejected_the_same_way(self, client, seeded):
        """Must not distinguish a bad username from a bad password."""
        response = client.post(
            f"{API}/auth/login", data={"username": "nobody", "password": "wrong"}
        )
        assert response.status_code == 401
        assert "Incorrect username or password" in response.json()["detail"]

    def test_self_registration_cannot_claim_an_official_role(self, client, seeded):
        response = client.post(
            f"{API}/auth/register",
            json={
                "username": "impostor",
                "full_name": "Not An Officer",
                "password": "password123",
                "role": "district_officer",
            },
        )
        assert response.status_code == 403

    def test_citizen_self_registration_is_allowed(self, client, seeded):
        response = client.post(
            f"{API}/auth/register",
            json={
                "username": "newcitizen",
                "full_name": "New Citizen",
                "password": "password123",
                "role": "citizen",
                "district": "Aizawl",
            },
        )
        assert response.status_code == 201
        assert response.json()["user"]["role"] == "citizen"

    def test_protected_endpoint_requires_a_token(self, client, seeded):
        assert client.get(f"{API}/auth/users").status_code == 401


class TestZones:
    def test_lists_all_seeded_zones(self, client, seeded):
        zones = client.get(f"{API}/zones").json()
        assert len(zones) == 37
        assert {z["state"] for z in zones} >= {"Sikkim", "Mizoram", "Meghalaya"}

    def test_filters_by_state(self, client, seeded):
        zones = client.get(f"{API}/zones", params={"state": "Mizoram"}).json()
        assert zones
        assert all(z["state"] == "Mizoram" for z in zones)

    def test_heatmap_returns_geometry_for_the_map(self, client, seeded):
        points = client.get(f"{API}/zones/heatmap").json()
        assert len(points) == 37
        assert all(p["geometry"]["type"] == "Polygon" for p in points)

    def test_unknown_zone_is_404(self, client, seeded):
        assert client.get(f"{API}/zones/999999").status_code == 404

    def test_seeded_zones_are_physically_consistent(self, client, seeded):
        """Every seeded slope must be stable when dry, or the model is wrong."""
        from app.ml.features import ROOT_COHESION_KPA
        from app.ml.physics import factor_of_safety, seismic_amplification
        from app.models.geo import Zone

        for zone in seeded.query(Zone).all():
            fos = factor_of_safety(
                zone.slope_deg, zone.soil_depth_m, zone.cohesion_kpa,
                zone.friction_angle_deg, wetness=0.0,
                root_cohesion_kpa=ROOT_COHESION_KPA.get(zone.land_cover, 1.0),
                suction_cohesion_kpa=zone.suction_cohesion_kpa,
            ) / seismic_amplification(zone.seismic_zone, zone.slope_deg)
            assert fos >= 1.0, f"{zone.code} is unstable when dry (FoS {fos:.2f})"


class TestFieldReports:
    def _payload(self, **overrides):
        data = {
            "latitude": 23.7271,
            "longitude": 92.7176,
            "category": "crack",
            "severity": 3,
        }
        data.update(overrides)
        return data

    def test_anyone_can_submit_without_signing_in(self, client, seeded):
        response = client.post(f"{API}/reports", data=self._payload())
        assert response.status_code == 201
        assert response.json()["status"] == "pending"

    def test_report_is_attached_to_the_nearest_zone(self, client, seeded):
        body = client.post(f"{API}/reports", data=self._payload()).json()
        assert body["zone_id"] is not None

    def test_a_report_far_from_any_zone_has_no_zone(self, client, seeded):
        # Inside the NER bounding box but far from every monitored slope.
        body = client.post(
            f"{API}/reports", data=self._payload(latitude=21.0, longitude=97.5)
        ).json()
        assert body["zone_id"] is None

    def test_coordinates_outside_the_region_are_rejected(self, client, seeded):
        response = client.post(
            f"{API}/reports", data=self._payload(latitude=51.5, longitude=-0.12)
        )
        assert response.status_code == 422

    def test_severity_is_bounded(self, client, seeded):
        assert client.post(f"{API}/reports", data=self._payload(severity=99)).status_code == 422
        assert client.post(f"{API}/reports", data=self._payload(severity=0)).status_code == 422

    def test_replaying_a_client_uuid_does_not_duplicate(self, client, seeded):
        """The central offline guarantee: a retried upload creates one row."""
        payload = self._payload(client_uuid="fixed-uuid-1")
        first = client.post(f"{API}/reports", data=payload).json()
        second = client.post(f"{API}/reports", data=payload).json()
        assert first["id"] == second["id"]
        assert len(client.get(f"{API}/reports").json()) == 1

    def test_verification_requires_an_official(self, client, seeded):
        report = client.post(f"{API}/reports", data=self._payload()).json()

        anonymous = client.patch(
            f"{API}/reports/{report['id']}/verify", json={"status": "verified"}
        )
        assert anonymous.status_code == 401

        token = client.post(
            f"{API}/auth/login", data={"username": "citizen.aizawl", "password": "prahari123"}
        ).json()["access_token"]
        citizen = client.patch(
            f"{API}/reports/{report['id']}/verify",
            json={"status": "verified"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert citizen.status_code == 403

    def test_official_can_verify(self, client, seeded, admin_headers):
        report = client.post(f"{API}/reports", data=self._payload()).json()
        response = client.patch(
            f"{API}/reports/{report['id']}/verify",
            json={"status": "verified", "note": "Site visit confirmed."},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "verified"


class TestOfflineSync:
    def test_bundle_contains_everything_needed_offline(self, client, seeded):
        bundle = client.get(f"{API}/sync/bundle").json()
        assert bundle["zones"] and bundle["roads"] is not None
        assert bundle["languages"]
        assert bundle["cache_ttl_minutes"] > 0
        assert all("geometry" in z for z in bundle["zones"])

    def test_bundle_can_be_scoped_to_a_state(self, client, seeded):
        bundle = client.get(f"{API}/sync/bundle", params={"state": "Mizoram"}).json()
        assert all(z["state"] == "Mizoram" for z in bundle["zones"])

    def test_push_accepts_a_batch(self, client, seeded):
        response = client.post(
            f"{API}/sync/push",
            json={
                "reports": [
                    {"client_uuid": "q1", "latitude": 23.73, "longitude": 92.72,
                     "category": "crack", "severity": 2},
                    {"client_uuid": "q2", "latitude": 25.17, "longitude": 93.02,
                     "category": "road_block", "severity": 5},
                ]
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 2
        assert body["duplicates"] == 0

    def test_push_is_idempotent(self, client, seeded):
        batch = {
            "reports": [
                {"client_uuid": "dup-1", "latitude": 23.73, "longitude": 92.72,
                 "category": "crack", "severity": 2}
            ]
        }
        client.post(f"{API}/sync/push", json=batch)
        second = client.post(f"{API}/sync/push", json=batch).json()
        assert second["accepted"] == 0
        assert second["duplicates"] == 1

    def test_one_bad_report_does_not_reject_the_batch(self, client, seeded):
        """A malformed row must not cost the queue its good rows."""
        response = client.post(
            f"{API}/sync/push",
            json={
                "reports": [
                    {"client_uuid": "good-1", "latitude": 23.73, "longitude": 92.72,
                     "category": "crack", "severity": 2},
                    {"client_uuid": "bad-1", "latitude": 51.5, "longitude": -0.12,
                     "category": "crack", "severity": 2},
                ]
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 1
        assert len(body["rejected"]) == 1
        assert body["rejected"][0]["client_uuid"] == "bad-1"


class TestSensors:
    def test_gateway_batch_ingest(self, client, seeded, admin_headers):
        stations = client.get(f"{API}/sensors/stations").json()
        assert stations
        code = stations[0]["code"]

        response = client.post(
            f"{API}/sensors/readings",
            json={"readings": [
                {"station_code": code, "soil_moisture_pct": 41.0, "tilt_deg": 0.9},
                {"station_code": "DOES-NOT-EXIST", "soil_moisture_pct": 10.0},
            ]},
            headers=admin_headers,
        )
        assert response.status_code == 202
        body = response.json()
        assert body["accepted"] == 1
        assert body["unknown_stations"] == ["DOES-NOT-EXIST"]

    def test_ingest_requires_authorisation(self, client, seeded):
        response = client.post(
            f"{API}/sensors/readings",
            json={"readings": [{"station_code": "X", "soil_moisture_pct": 10}]},
        )
        assert response.status_code == 401

    def test_out_of_range_readings_are_rejected(self, client, seeded, admin_headers):
        response = client.post(
            f"{API}/sensors/readings",
            json={"readings": [{"station_code": "X", "soil_moisture_pct": 500}]},
            headers=admin_headers,
        )
        assert response.status_code == 422


class TestAlerts:
    def test_alert_languages_report_review_status(self, client, seeded):
        body = client.get(f"{API}/alerts/languages").json()
        assert len(body["languages"]) == 8
        # English is the source language; every translation is unreviewed.
        assert body["review"]["pending_review"]
        assert "en" not in body["review"]["pending_review"]
        assert body["review"]["warning"]

    def test_manual_alert_requires_an_official(self, client, seeded):
        zone = client.get(f"{API}/zones").json()[0]
        response = client.post(
            f"{API}/alerts", json={"zone_id": zone["id"], "body": "A manual test bulletin."}
        )
        assert response.status_code == 401

    def test_district_officer_cannot_alert_outside_their_district(self, client, seeded):
        token = client.post(
            f"{API}/auth/login", data={"username": "dc.aizawl", "password": "prahari123"}
        ).json()["access_token"]
        meghalaya = client.get(f"{API}/zones", params={"state": "Meghalaya"}).json()[0]

        response = client.post(
            f"{API}/alerts",
            json={"zone_id": meghalaya["id"], "body": "Out-of-jurisdiction attempt."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestRoads:
    def test_connectivity_summary(self, client, seeded):
        body = client.get(f"{API}/roads/connectivity").json()
        assert body["total_segments"] == 25
        assert body["total_km"] > 2000
        assert set(body["by_status"]) == {"open", "restricted", "blocked"}

    def test_status_update_requires_authorisation(self, client, seeded):
        road = client.get(f"{API}/roads").json()[0]
        response = client.patch(f"{API}/roads/{road['id']}/status", json={"status": "blocked"})
        assert response.status_code == 401

    def test_official_can_set_status(self, client, seeded, admin_headers):
        road = client.get(f"{API}/roads").json()[0]
        response = client.patch(
            f"{API}/roads/{road['id']}/status",
            json={"status": "blocked", "note": "Debris across carriageway"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "blocked"


class TestDashboard:
    def test_summary_reports_data_provenance(self, client, seeded):
        """The dashboard must always say whether its inputs are real."""
        body = client.get(f"{API}/dashboard/summary").json()
        assert body["zones_monitored"] == 37
        assert "weather" in body["data_sources"]
        assert "sms" in body["data_sources"]
        assert body["data_sources"]["weather"]["note"]  # simulator warning present

    def test_model_info_declares_synthetic_training(self, client, seeded):
        body = client.get(f"{API}/dashboard/model").json()
        if body["available"] and body.get("is_synthetic"):
            assert body["caveat"]
            assert "synthetic" in body["data_source"].lower()

    def test_health_reports_components(self, client):
        body = client.get("/health").json()
        assert body["status"] in ("healthy", "degraded")
        assert "database" in body["components"]
        assert "ml_model" in body["components"]


class TestDrill:
    def test_drill_requires_authorisation(self, client, seeded):
        assert client.post(f"{API}/dashboard/drill", json={"intensity": "heavy"}).status_code == 401

    def test_drill_raises_risk_and_is_reversible(self, client, seeded, admin_headers):
        before = client.get(f"{API}/dashboard/summary").json()

        response = client.post(
            f"{API}/dashboard/drill",
            json={"intensity": "extreme", "duration_hours": 48, "issue_alerts": False},
            headers=admin_headers,
        )
        assert response.status_code == 202
        during = client.get(f"{API}/dashboard/summary").json()
        assert during["data_sources"]["drill"]["active"] is True

        elevated_before = before["risk_distribution"]["high"] + before["risk_distribution"]["critical"]
        elevated_during = during["risk_distribution"]["high"] + during["risk_distribution"]["critical"]
        assert elevated_during > elevated_before

        cleared = client.delete(f"{API}/dashboard/drill", headers=admin_headers)
        assert cleared.status_code == 200
        after = client.get(f"{API}/dashboard/summary").json()
        assert after["data_sources"]["drill"]["active"] is False

    def test_unknown_intensity_is_rejected(self, client, seeded, admin_headers):
        response = client.post(
            f"{API}/dashboard/drill", json={"intensity": "apocalypse"}, headers=admin_headers
        )
        assert response.status_code == 422


class TestTimezoneHandling:
    def test_naive_database_values_compare_safely(self, client, seeded):
        """Regression: SQLite returns naive datetimes.

        Comparing those against `datetime.now(timezone.utc)` in Python raises
        TypeError, which took down the whole dashboard summary while every
        query behind it still succeeded.
        """
        from app.utils.timeutil import as_utc

        # What SQLite hands back: a UTC wall-clock value with no tzinfo.
        naive = datetime.now(timezone.utc).replace(tzinfo=None)
        aware = datetime.now(timezone.utc)
        assert as_utc(naive) <= aware + timedelta(seconds=5)
        assert as_utc(aware) == aware
        assert as_utc(None) is None

        # The endpoint that actually broke.
        assert client.get(f"{API}/dashboard/summary").status_code == 200
