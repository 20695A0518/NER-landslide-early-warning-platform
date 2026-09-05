# Data sources: what is real, what is not

Every figure this platform displays comes from one of four places. This document
says which, so nothing here can be mistaken for measurement it is not.

## 1. Real, verifiable

| Data | Source | Notes |
| --- | --- | --- |
| Zone centroids | Named settlements and highway corridors | Real coordinates |
| District / state assignment | Administrative geography | Correct as of 2024 |
| Road alignments | NH/SH numbering and endpoints | Real routes; polylines are coarse |
| Seismic zone | IS-1893 macro-zonation | NER is zone V; Sikkim is IV |
| Language / state mapping | Official state languages | See translation-signoff.md |

## 2. Representative defaults, not survey data

Terrain attributes in `backend/app/data/ner_zones.py` — slope, regolith depth,
friction angle, cohesion, lithology, land cover, NDVI, annual rainfall — are
plausible values for each locality drawn from published regional
characterisations. They are calibration defaults chosen so the physics behaves
correctly, **not** measurements of those specific slopes.

Replace before operational use:

| Attribute | Replace with |
| --- | --- |
| `slope_deg`, `aspect_deg`, `curvature`, `elevation_m` | Cartosat / SRTM DEM derivatives |
| `lithology` | Geological Survey of India 1:50,000 mapping |
| `soil_type`, `soil_depth_m` | NBSS&LUP soil survey |
| `friction_angle_deg`, `cohesion_kpa` | Site investigation / laboratory shear tests |
| `ndvi`, `land_cover` | Sentinel-2 or Bhuvan LULC |
| `annual_rainfall_mm` | IMD gridded 0.25° long-period average |
| `hill_cutting_index` | Change detection on time-series imagery |
| `population`, `villages` | Census 2011 village directory, updated |
| `geometry` | Watershed segmentation of the DEM into slope units |

The zone footprints currently drawn on the map are correctly-sized hexagons
around each centroid, not mapped slope-unit boundaries.

## 3. Simulated

Active whenever the corresponding key is absent. Each is labelled in the API
response and in the UI.

| Data | Module | Labelled as |
| --- | --- | --- |
| Rainfall and weather | `services/weather.py` | `source="simulator"` |
| Sensor telemetry | `services/sensors.py` | `SIMULATE_SENSORS=true` |
| SMS delivery | `services/notifications.py` | `provider="console"` |

The rainfall simulator draws one daily total per zone-day from a heavy-tailed
distribution scaled by that zone's climatology, then distributes it across hours.
It is autocorrelated across days and varies strongly between zones, because
monsoon systems park over one district and miss the next. It is a plausible
generator, not a forecast.

## 4. Synthetic

| Data | Module | Stamped |
| --- | --- | --- |
| Historical landslide inventory | `services/seed.py` | `source="synthetic seed"` |
| Model training set | `ml/dataset.py` | `is_synthetic: true` in metrics |
| Drill / exercise rainfall | `services/drill.py` | `source="drill"` |

**The historical inventory is generated, not recorded.** Events are sampled from
each zone's susceptibility and weighted toward monsoon months so the map and the
"past events" panel are internally coherent. It must not be cited as a record of
actual landslides. Delete every row with `source='synthetic seed'` before
importing a real inventory.

**Model metrics measure recovery of the synthetic generating process**, not
real-world landslide prediction skill. `app/ml/train.py` records this in
`data_source` and `caveat`, and `/api/v1/dashboard/model` returns both.

## Importing real data

```bash
# Retrain on a mapped inventory
python -m app.ml.train --data path/to/inventory.csv
```

The CSV needs every column in `app.ml.features.FEATURE_ORDER` plus a binary
`label`. Clear the synthetic inventory first:

```sql
DELETE FROM historical_landslides WHERE source = 'synthetic seed';
```

Then re-run the seeder to recompute `historical_event_count` per zone, since the
model uses it as a prior.
