"""Stage 4 entry point: train the ordinal bracket model on labeled decks,
report accuracy + sanity gates + feature importances (every run, so a
regression is immediately visible), then score every deck in the corpus
(labeled or not) into deck_scores.

The two cross-checks against external verdicts (Spellbook's own
/estimate-bracket, Archidekt's own price field) are deliberately NOT part
of this hot path -- they're occasional, sample-based checks per the spec
("call it on a sample... compare"), not something to re-run and wait on
every time the model refits. Run them separately via
bracket_ranker.calibrate.spellbook_crosscheck / analyze.price_crosscheck
(the web UI exposes both as their own buttons).
"""
from __future__ import annotations

import json
import pickle
import sqlite3
from datetime import datetime, timezone

import numpy as np

from bracket_ranker.config import MODEL_PATH
from bracket_ranker.calibrate.fit import (
    OrdinalBracketModel,
    build_feature_matrix,
    print_feature_importances,
    report_accuracy,
    sanity_gates,
    train_test_split_decks,
)
from bracket_ranker.calibrate.labels import load_labeled_decks, report_label_conflicts
from bracket_ranker.db import connect


def _score_all_decks(conn: sqlite3.Connection, model: OrdinalBracketModel) -> int:
    rows = conn.execute(
        """
        SELECT d.fingerprint, d.declared_bracket, d.source,
               s.bracket_floor, s.game_changer_count, s.has_mass_land_denial,
               s.combo_count, s.top_combo_relevance, s.median_assembly_turn,
               s.tutor_count, s.interaction_count, s.avg_mana_value,
               s.ramp_count, s.fast_mana_count
        FROM decks d
        JOIN deck_signals s ON s.fingerprint = d.fingerprint
        """
    ).fetchall()
    if not rows:
        return 0

    from bracket_ranker.calibrate.labels import LabeledDeck  # local import: reuse feature-building
    pseudo = [
        LabeledDeck(
            fingerprint=r["fingerprint"], source=r["source"],
            declared_bracket_raw=r["declared_bracket"] or 0,
            declared_bracket_used=r["declared_bracket"] or 0,
            label_conflict=False,
            bracket_floor=r["bracket_floor"],
            features={
                "game_changer_count": r["game_changer_count"],
                "has_mass_land_denial": r["has_mass_land_denial"],
                "bracket_floor": r["bracket_floor"],
                "combo_count": r["combo_count"],
                "top_combo_relevance": r["top_combo_relevance"],
                "median_assembly_turn": r["median_assembly_turn"],
                "tutor_count": r["tutor_count"],
                "interaction_count": r["interaction_count"],
                "avg_mana_value": r["avg_mana_value"],
                "ramp_count": r["ramp_count"],
                "fast_mana_count": r["fast_mana_count"],
            },
        )
        for r in rows
    ]
    X = build_feature_matrix(pseudo)
    feel_score, feel_bracket, confidence = model.predict(X)

    out = []
    for row, score, bracket, conf in zip(rows, feel_score, feel_bracket, confidence):
        reasons = []
        if row["combo_count"] == 0:
            reasons.append("zero-combo deck: assembly-turn/combo signals are absent, "
                            "not just low -- score is likely underconfident")
        declared_raw = row["declared_bracket"]
        out.append((
            row["fingerprint"],
            declared_raw,
            max(declared_raw, row["bracket_floor"]) if declared_raw is not None else None,
            int(declared_raw is not None and declared_raw < row["bracket_floor"]),
            float(score),
            int(bracket),
            float(conf),
            "; ".join(reasons) or None,
        ))

    conn.execute("DELETE FROM deck_scores")
    conn.executemany(
        """INSERT INTO deck_scores (
            fingerprint, declared_bracket_raw, declared_bracket_used, label_conflict,
            feel_score, feel_bracket, confidence, low_confidence_reason
        ) VALUES (?,?,?,?,?,?,?,?)""",
        out,
    )
    return len(out)


def main() -> None:
    with connect() as conn:
        labeled = load_labeled_decks(conn)
        print(f"[calibrate] {len(labeled)} labeled decks available for training")
        report_label_conflicts(labeled)
        if len(labeled) < 20:
            print("[calibrate] WARNING: too few labeled decks for a meaningful split; "
                  "run Stage 1 with larger targets before trusting these numbers")
            if len(labeled) == 0:
                return

        X_train, y_train, train_decks, X_test, y_test, test_decks = train_test_split_decks(labeled)
        eval_model = OrdinalBracketModel()
        eval_model.fit(X_train, y_train)
        _, pred_bracket_train, _ = eval_model.predict(X_train)
        _, pred_bracket_test, _ = eval_model.predict(X_test)
        train_exact, train_within_one = report_accuracy(y_train, pred_bracket_train, "train")
        test_exact, test_within_one = report_accuracy(y_test, pred_bracket_test, "test")
        gates = sanity_gates(test_decks, pred_bracket_test)
        print_feature_importances(eval_model)

        final_model = OrdinalBracketModel()
        X_all = build_feature_matrix(labeled)
        y_all = np.array([d.declared_bracket_used for d in labeled])
        final_model.fit(X_all, y_all)

        n = _score_all_decks(conn, final_model)
        print(f"[calibrate] scored {n} decks into deck_scores")

        conflict_rate = (
            sum(1 for d in labeled if d.label_conflict) / len(labeled) if labeled else None
        )
        importances = [
            {"threshold": k, "coefficients": [[name, float(coef)] for name, coef in pairs]}
            for k, pairs in final_model.feature_importances()
        ]
        conn.execute(
            """INSERT INTO calibration_runs (
                run_at, n_labeled, label_conflict_rate, train_exact_match, train_within_one,
                test_exact_match, test_within_one, precon_gate_pass, precon_gate_rate,
                cedh_gate_pass, cedh_gate_rate, feature_importances
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(), len(labeled), conflict_rate,
                train_exact, train_within_one, test_exact, test_within_one,
                gates.precon_pass, gates.precon_rate, gates.cedh_pass, gates.cedh_rate,
                json.dumps(importances),
            ),
        )
        print("[calibrate] persisted this run's metrics to calibration_runs")

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(final_model, f)
    print(f"[calibrate] persisted fitted model to {MODEL_PATH} (for `rank --explain`)")


if __name__ == "__main__":
    main()
