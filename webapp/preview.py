"""Deck preview: bracket/cost/combo facts for the Simulate tab's deck
pickers -- the same kind of facts Explorer shows, but for ANY deck,
including one imported seconds ago that hasn't been through a full
Analyze/Calibrate run yet (deck import only runs Stage 2/resolve, by
design -- see webapp/actions.py -- to stay fast).

Lives in webapp/, not bracket_ranker/analyze/, specifically because it
needs run_analyze.analyze_deck() (a script-level function) for the
on-demand fallback path; bracket_ranker/analyze/deck_library.py can't
import that itself without a circular import, since run_analyze.py
already imports FROM deck_library.
"""
from __future__ import annotations

import pickle
import sqlite3

import numpy as np

import run_analyze
from bracket_ranker.analyze.deck_library import build_full_simulation_inputs
from bracket_ranker.calibrate.fit import OrdinalBracketModel, feature_vector_from_signals
from bracket_ranker.config import MODEL_PATH


def _try_load_model() -> OrdinalBracketModel | None:
    try:
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None


def build_deck_preview(
    conn: sqlite3.Connection,
    fingerprint: str,
    lookup: dict,
    combo_index,
    owned_oracle_ids: set[str],
    basic_land_ids: set[str],
) -> dict | None:
    deck = conn.execute("SELECT * FROM decks WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if deck is None:
        return None

    signals_row = conn.execute(
        "SELECT * FROM deck_signals WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    if signals_row is not None:
        signals = dict(signals_row)
        signals_computed_fresh = False
    else:
        # Not yet analyzed (e.g. imported moments ago) -- compute Stage 3's
        # signals for this one deck on the spot rather than telling the
        # user to wait for a corpus-wide Analyze run just to see facts
        # about their own deck.
        signals = run_analyze.analyze_deck(
            conn, deck, combo_index, owned_oracle_ids, basic_land_ids, lookup
        )
        signals_computed_fresh = True

    score_row = conn.execute(
        "SELECT * FROM deck_scores WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    feel_bracket = feel_score = confidence = None
    score_source = None
    if score_row is not None:
        feel_bracket = score_row["feel_bracket"]
        feel_score = score_row["feel_score"]
        confidence = score_row["confidence"]
        score_source = "stored"
    else:
        model = _try_load_model()
        if model is not None:
            x = feature_vector_from_signals(signals)
            score, bracket, conf = model.predict(np.array([x]))
            feel_score, feel_bracket, confidence = float(score[0]), int(bracket[0]), float(conf[0])
            score_source = "computed_on_demand"

    # deck_signals only stores the single top-ranked combo -- the full
    # list (with card names) always gets recomputed here regardless of
    # the fast/slow path above. Cheap: no simulation, just the same
    # set-intersection combo detection stress_test/table_sim already do.
    sim_inputs = build_full_simulation_inputs(conn, fingerprint, lookup, combo_index)
    combos = [
        {
            "variant_id": t.variant_id, "piece_count": t.piece_count,
            "is_game_ender": t.is_game_ender, "is_infinite": t.is_infinite,
            "card_names": list(t.card_names),
        }
        for t in (sim_inputs.combo_targets if sim_inputs else [])
    ]

    return {
        "fingerprint": fingerprint,
        "commander_name": deck["commander_name"],
        "source": deck["source"],
        "source_url": deck["source_url"],
        "pct_owned": signals["pct_owned"],
        "usd_to_complete": signals["usd_to_complete"],
        "bracket_floor": signals["bracket_floor"],
        "game_changer_count": signals["game_changer_count"],
        "has_mass_land_denial": bool(signals["has_mass_land_denial"]),
        "combo_count": signals["combo_count"],
        "feel_bracket": feel_bracket,
        "feel_score": feel_score,
        "confidence": confidence,
        "score_source": score_source,  # "stored" | "computed_on_demand" | None (no model trained yet)
        "signals_computed_fresh": signals_computed_fresh,
        "combos": combos,
    }
