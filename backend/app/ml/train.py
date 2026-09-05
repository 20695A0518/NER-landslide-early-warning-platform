"""Train and persist the landslide susceptibility model.

    python -m app.ml.train                     # synthetic training set
    python -m app.ml.train --data inv.csv      # real inventory
    python -m app.ml.train --samples 40000     # larger synthetic run

Writes two artifacts next to this file:

    artifacts/model.joblib   calibrated classifier + feature order + scaler
    artifacts/metrics.json   held-out metrics, feature importances, provenance

`metrics.json` records `data_source`, and every surface that reports accuracy
(the API `/model/info` endpoint and the dashboard footer) reads that field and
labels synthetic results as such. Do not remove it.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from app.ml.dataset import generate_training_frame
from app.ml.features import FEATURE_LABELS, FEATURE_ORDER

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "model.joblib"
METRICS_PATH = ARTIFACT_DIR / "metrics.json"
MODEL_VERSION = "prahari-hgb-v1"


def _load_csv(path: Path) -> tuple[np.ndarray, np.ndarray, str]:
    import pandas as pd

    df = pd.read_csv(path)
    missing = [c for c in FEATURE_ORDER if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{path} is missing required feature columns: {', '.join(missing)}\n"
            "See app/ml/dataset.py for the expected schema."
        )
    if "label" not in df.columns:
        raise SystemExit(f"{path} has no `label` column (1 = failure, 0 = no failure).")
    X = df[FEATURE_ORDER].to_numpy(dtype=np.float64)
    y = df["label"].to_numpy(dtype=np.int64)
    return X, y, f"real inventory: {path.name}"


def train(
    n_samples: int = 24000,
    seed: int = 20240915,
    csv_path: Path | None = None,
) -> dict:
    if csv_path:
        X, y, data_source = _load_csv(csv_path)
    else:
        X, y, _ = generate_training_frame(n_samples=n_samples, seed=seed)
        data_source = "SYNTHETIC (app.ml.dataset) - not validated against real events"

    positives = int(y.sum())
    if positives < 20 or positives == len(y):
        raise SystemExit(
            f"Degenerate training set: {positives} positives out of {len(y)} rows."
        )

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.22, random_state=seed, stratify=y
    )

    # Histogram gradient boosting handles the mixed-scale tabular features
    # without a scaler and trains fast enough to retrain on every data refresh.
    base = HistGradientBoostingClassifier(
        max_iter=320,
        learning_rate=0.06,
        max_depth=6,
        min_samples_leaf=28,
        l2_regularization=0.9,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=seed,
    )

    # Early warning is only useful if the probability is trustworthy as a
    # number - "0.7" has to mean roughly seven times in ten, because the
    # evacuation thresholds are set on it. Isotonic calibration on held-out
    # folds is what makes that true.
    model = CalibratedClassifierCV(base, method="isotonic", cv=4)
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_test, preds, labels=[0, 1]).ravel()

    # Operating point: recall matters far more than precision here. Missing a
    # slope that fails costs lives; a false alarm costs an inspection visit.
    # Pick the lowest threshold that still holds precision above 0.25.
    operating_threshold, operating_recall = 0.5, float(recall_score(y_test, preds, zero_division=0))
    for candidate in np.arange(0.10, 0.75, 0.01):
        cand_preds = (proba >= candidate).astype(int)
        prec = precision_score(y_test, cand_preds, zero_division=0)
        if prec >= 0.25:
            operating_threshold = float(candidate)
            operating_recall = float(recall_score(y_test, cand_preds, zero_division=0))
            break

    perm = permutation_importance(
        model, X_test, y_test, n_repeats=8, random_state=seed, scoring="roc_auc"
    )
    importances = sorted(
        (
            {
                "feature": name,
                "label": FEATURE_LABELS.get(name, name),
                "importance": round(float(mean), 6),
                "std": round(float(std), 6),
            }
            for name, mean, std in zip(
                FEATURE_ORDER, perm.importances_mean, perm.importances_std, strict=True
            )
        ),
        key=lambda d: d["importance"],
        reverse=True,
    )

    metrics = {
        "model_version": MODEL_VERSION,
        "algorithm": "HistGradientBoostingClassifier + isotonic calibration",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_source": data_source,
        "is_synthetic": csv_path is None,
        "n_samples": int(len(y)),
        "n_features": len(FEATURE_ORDER),
        "positive_rate": round(float(y.mean()), 4),
        "test_size": int(len(y_test)),
        "roc_auc": round(float(roc_auc_score(y_test, proba)), 4),
        "pr_auc": round(float(average_precision_score(y_test, proba)), 4),
        "brier_score": round(float(brier_score_loss(y_test, proba)), 4),
        "precision": round(float(precision_score(y_test, preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, preds, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, preds, zero_division=0)), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "operating_threshold": round(operating_threshold, 3),
        "operating_recall": round(operating_recall, 4),
        "feature_importances": importances,
        "caveat": (
            "Metrics measure recovery of the synthetic generating process, not "
            "real-world landslide prediction skill. Retrain on a mapped "
            "inventory before operational use."
        )
        if csv_path is None
        else "Metrics computed on a held-out split of the supplied inventory.",
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "feature_order": FEATURE_ORDER,
            "model_version": MODEL_VERSION,
            "operating_threshold": operating_threshold,
            "is_synthetic": csv_path is None,
        },
        MODEL_PATH,
    )
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the PRAHARI susceptibility model.")
    parser.add_argument("--data", type=Path, help="CSV with FEATURE_ORDER columns + label")
    parser.add_argument("--samples", type=int, default=24000, help="synthetic sample count")
    parser.add_argument("--seed", type=int, default=20240915)
    args = parser.parse_args()

    metrics = train(n_samples=args.samples, seed=args.seed, csv_path=args.data)

    print(f"\n  Model      {metrics['model_version']}  ({metrics['algorithm']})")
    print(f"  Data       {metrics['data_source']}")
    print(f"  Samples    {metrics['n_samples']:,}  ({metrics['positive_rate']:.1%} positive)")
    print(f"  ROC-AUC    {metrics['roc_auc']}")
    print(f"  PR-AUC     {metrics['pr_auc']}")
    print(f"  Brier      {metrics['brier_score']}   (lower is better; calibration)")
    print(f"  Precision  {metrics['precision']}      Recall  {metrics['recall']}")
    print(
        f"  Operating threshold {metrics['operating_threshold']} "
        f"-> recall {metrics['operating_recall']}"
    )
    print("\n  Top predictors:")
    for row in metrics["feature_importances"][:8]:
        print(f"    {row['importance']:>8.4f}  {row['label']}")
    if metrics["is_synthetic"]:
        print(f"\n  ! {metrics['caveat']}")
    print(f"\n  Saved {MODEL_PATH.name} and {METRICS_PATH.name}\n")


if __name__ == "__main__":
    main()
