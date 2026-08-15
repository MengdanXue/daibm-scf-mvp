from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def evaluate_binary_predictions(
    labels: np.ndarray,
    probabilities: np.ndarray,
    threshold: float = 0.5,
) -> dict[str, Any]:
    truth = np.asarray(labels, dtype=np.uint8).reshape(-1)
    scores = np.asarray(probabilities, dtype=np.float64).reshape(-1)
    predicted = (scores >= threshold).astype(np.uint8)
    return {
        "roc_auc": float(roc_auc_score(truth, scores)),
        "pr_auc": float(average_precision_score(truth, scores)),
        "f1": float(f1_score(truth, predicted, zero_division=0)),
        "precision": float(
            precision_score(truth, predicted, zero_division=0)
        ),
        "recall": float(recall_score(truth, predicted, zero_division=0)),
        "confusion_matrix": confusion_matrix(
            truth, predicted, labels=(0, 1)
        ).astype(int).tolist(),
        "brier_score": float(brier_score_loss(truth, scores)),
    }
