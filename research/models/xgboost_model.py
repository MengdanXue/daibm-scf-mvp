from __future__ import annotations

from dataclasses import asdict, dataclass

from xgboost import XGBClassifier


@dataclass(frozen=True)
class XGBoostConfig:
    max_depth: int = 4
    learning_rate: float = 0.05
    n_estimators: int = 200
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    eval_metric: str = "auc"
    random_state: int = 20260815

    def to_dict(self) -> dict[str, int | float | str]:
        return asdict(self)

    def build(self) -> XGBClassifier:
        return XGBClassifier(
            objective="binary:logistic",
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            n_estimators=self.n_estimators,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            eval_metric=self.eval_metric,
            random_state=self.random_state,
            n_jobs=1,
            tree_method="hist",
        )
