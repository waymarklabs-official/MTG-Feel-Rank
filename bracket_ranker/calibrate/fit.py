"""Stage 4.3/4.4: fit an interpretable ordinal-style model and report
accuracy every run.

"Interpretable" per the spec means the user needs to be able to explain a
score, not just trust it -- so this is deliberately NOT a black box. The
model is a stack of five independent binary logistic regressions, one per
threshold k in {2,3,4,5}, each predicting P(bracket >= k) from the same
feature vector (a standard, simple way to get an ordinal model out of an
off-the-shelf classifier: https://en.wikipedia.org/wiki/Ordinal_regression
"cumulative link" approach). Per-class probabilities come from differencing
adjacent thresholds; feel_score is their expectation; feel_bracket is the
most likely class. Every prediction's "why" is just five logistic
regressions' worth of (feature, coefficient) pairs -- readable, arguable,
and reportable via feature_importances().

Collection-dependent columns (pct_owned, usd_to_complete) are deliberately
EXCLUDED from the feature set: a deck doesn't get more or less powerful
because of what's sitting in one specific person's binders, and training on
them would leak MY collection's contents into a supposedly general model.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from bracket_ranker.calibrate.labels import LabeledDeck

FEATURE_COLUMNS = [
    "game_changer_count",
    "has_mass_land_denial",
    "bracket_floor",
    "combo_count",
    "top_combo_relevance",
    "median_assembly_turn",
    "tutor_count",
    "interaction_count",
    "avg_mana_value",
    "ramp_count",
    "fast_mana_count",
]
# A deck with no detected combo has no median_assembly_turn at all (None,
# not "slow") -- imputed to a fixed value one worse than the Monte Carlo
# turn horizon, since "never assembles" IS the worst case, not a missing
# observation to be averaged away.
NO_COMBO_ASSEMBLY_SENTINEL = 16.0
THRESHOLDS = [2, 3, 4, 5]  # binary classifiers for "bracket >= k"


def feature_vector_from_signals(signals) -> list[float]:
    """Build one model input row from a Stage 3 signals dict (or anything
    dict-like -- a sqlite3.Row works too since it supports __getitem__).
    The single canonical place this mapping lives: previously duplicated
    between here and rank/explain.py's per-deck "why" breakdown, which
    risked the two silently drifting apart. Now both call this, and a
    third caller (the on-demand deck-preview endpoint, for decks that
    haven't been through a full Stage 3/4 run yet) reuses it too.
    """
    return [
        signals["game_changer_count"] or 0,
        signals["has_mass_land_denial"] or 0,
        signals["bracket_floor"] or 1,
        signals["combo_count"] or 0,
        signals["top_combo_relevance"] or 0.0,
        signals["median_assembly_turn"] if signals["median_assembly_turn"] is not None
        else NO_COMBO_ASSEMBLY_SENTINEL,
        signals["tutor_count"] or 0,
        signals["interaction_count"] or 0,
        signals["avg_mana_value"] or 0.0,
        signals["ramp_count"] or 0,
        signals["fast_mana_count"] or 0,
    ]


def build_feature_matrix(decks: list[LabeledDeck]) -> np.ndarray:
    return np.array([feature_vector_from_signals(d.features) for d in decks], dtype=float)


class OrdinalBracketModel:
    def __init__(self):
        self.threshold_models: dict[int, LogisticRegression] = {}

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        for k in THRESHOLDS:
            y_bin = (y >= k).astype(int)
            # A threshold with only one class present (e.g. no Bracket-5
            # decks in a small train split) can't fit a real boundary --
            # skip it; predict_proba treats a missing model as "never".
            if len(set(y_bin)) < 2:
                continue
            model = LogisticRegression(max_iter=2000)
            model.fit(X, y_bin)
            self.threshold_models[k] = model

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        n = X.shape[0]
        # P(bracket >= k) for k=1..6, clamped monotonic (k=1 always 1.0,
        # k=6 always 0.0 -- there is no bracket 6).
        p_ge = np.ones((n, 7))
        for k in THRESHOLDS:
            model = self.threshold_models.get(k)
            p_ge[:, k] = model.predict_proba(X)[:, 1] if model else 0.0
        p_ge[:, 6] = 0.0
        # Enforce monotonicity (P(>=k) should never exceed P(>=k-1); five
        # independently-fit models have no reason to respect that on their
        # own) by a running minimum before differencing into class probs.
        for k in range(2, 7):
            p_ge[:, k] = np.minimum(p_ge[:, k], p_ge[:, k - 1])
        class_probs = np.zeros((n, 6))  # index 1..5 used, 0 unused
        for k in range(1, 6):
            class_probs[:, k] = p_ge[:, k] - p_ge[:, k + 1]
        return class_probs[:, 1:6]  # columns 0..4 correspond to brackets 1..5

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        probs = self.predict_proba(X)
        brackets = np.array([1, 2, 3, 4, 5])
        feel_score = probs @ brackets
        feel_bracket = brackets[np.argmax(probs, axis=1)]
        confidence = probs.max(axis=1)
        return feel_score, feel_bracket, confidence

    def feature_importances(self) -> list[tuple[int, list[tuple[str, float]]]]:
        out = []
        for k in THRESHOLDS:
            model = self.threshold_models.get(k)
            if model is None:
                continue
            pairs = sorted(
                zip(FEATURE_COLUMNS, model.coef_[0]),
                key=lambda p: -abs(p[1]),
            )
            out.append((k, pairs))
        return out


def train_test_split_decks(decks: list[LabeledDeck], test_size: float = 0.25, seed: int = 42):
    X = build_feature_matrix(decks)
    y = np.array([d.declared_bracket_used for d in decks])
    idx = np.arange(len(decks))
    try:
        idx_train, idx_test = train_test_split(
            idx, test_size=test_size, random_state=seed, stratify=y
        )
    except ValueError:
        # A class with a single member can't be stratified -- fall back to
        # a plain random split rather than crashing on a small corpus.
        idx_train, idx_test = train_test_split(idx, test_size=test_size, random_state=seed)
    return (X[idx_train], y[idx_train], [decks[i] for i in idx_train],
            X[idx_test], y[idx_test], [decks[i] for i in idx_test])


def report_accuracy(y_true: np.ndarray, y_pred: np.ndarray, label: str) -> tuple[float, float]:
    exact = (y_true == y_pred).mean()
    within_one = (np.abs(y_true - y_pred) <= 1).mean()
    print(f"[calibrate] {label}: exact-match {exact:.1%}, within-one-bracket {within_one:.1%}")

    print(f"[calibrate] {label} confusion matrix (rows=true, cols=predicted, brackets 1-5):")
    matrix = np.zeros((5, 5), dtype=int)
    for t, p in zip(y_true, y_pred):
        matrix[int(t) - 1, int(p) - 1] += 1
    header = "        " + "".join(f"pred{k:>3} " for k in range(1, 6))
    print(header)
    for i, row in enumerate(matrix):
        print(f"true {i + 1}: " + "".join(f"{v:7d} " for v in row))
    return float(exact), float(within_one)


@dataclass
class SanityGateResult:
    precon_pass: bool | None   # None = gate skipped (no precons in this split)
    precon_rate: float | None
    cedh_pass: bool | None
    cedh_rate: float | None


def sanity_gates(decks: list[LabeledDeck], y_pred: np.ndarray) -> SanityGateResult:
    result = SanityGateResult(None, None, None, None)

    precon_idx = [i for i, d in enumerate(decks) if d.source == "mtgjson_precon"]
    if precon_idx:
        precon_preds = y_pred[precon_idx]
        rate = float(np.isin(precon_preds, [1, 2]).mean())
        result.precon_rate, result.precon_pass = rate, rate >= 0.8
        print(f"[calibrate] sanity gate -- precons should score 1-2: "
              f"{rate:.1%} of {len(precon_idx)} precons do "
              f"[{'PASS' if result.precon_pass else 'FAIL'}]")
    else:
        print("[calibrate] sanity gate -- no precons in this split, skipped")

    cedh_idx = [i for i, d in enumerate(decks) if d.source == "edhtop16"]
    if cedh_idx:
        cedh_preds = y_pred[cedh_idx]
        rate = float((cedh_preds == 5).mean())
        result.cedh_rate, result.cedh_pass = rate, rate >= 0.8
        print(f"[calibrate] sanity gate -- EDHTop16 decks should score 5: "
              f"{rate:.1%} of {len(cedh_idx)} do [{'PASS' if result.cedh_pass else 'FAIL'}]")
    else:
        print("[calibrate] sanity gate -- no EDHTop16 decks in this split, skipped")

    return result


def print_feature_importances(model: OrdinalBracketModel) -> None:
    print("[calibrate] feature importances (coefficient magnitude, per threshold model):")
    for k, pairs in model.feature_importances():
        print(f"  P(bracket >= {k}):")
        for name, coef in pairs[:6]:
            print(f"    {name:<22} {coef:+.3f}")
