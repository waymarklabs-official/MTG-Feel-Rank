"""Stage 5.3: per-deck explanation -- "I need to be able to argue with the
number." Loads the persisted Stage 4 model and shows exactly which signals
pushed the score up or down at each ordinal threshold, plus the combo and
assembly-curve detail a human actually cares about.

build_explanation() assembles the data as a plain dict; explain_deck() (the
CLI) and the web API's /api/decks/<fingerprint> route both just format the
same dict two different ways, so the two surfaces can never drift apart.
"""
from __future__ import annotations

import pickle
import sqlite3

from bracket_ranker.calibrate.fit import FEATURE_COLUMNS, THRESHOLDS, feature_vector_from_signals
from bracket_ranker.config import MODEL_PATH


def load_model():
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def _feature_contributions(signals: sqlite3.Row) -> list[dict] | None:
    try:
        model = load_model()
    except FileNotFoundError:
        return None
    x = feature_vector_from_signals(signals)
    by_threshold = []
    for k in THRESHOLDS:
        clf = model.threshold_models.get(k)
        if clf is None:
            continue
        contributions = sorted(
            zip(FEATURE_COLUMNS, x, clf.coef_[0]),
            key=lambda t: -abs(t[1] * t[2]),
        )
        by_threshold.append({
            "threshold": k,
            "contributions": [
                {
                    "feature": name, "value": value, "coefficient": coef,
                    "contribution": value * coef,
                    "sentinel": name == "median_assembly_turn" and signals["median_assembly_turn"] is None,
                }
                for name, value, coef in contributions[:6]
            ],
        })
    return by_threshold


def build_explanation(conn: sqlite3.Connection, fingerprint: str) -> dict | None:
    deck = conn.execute("SELECT * FROM decks WHERE fingerprint = ?", (fingerprint,)).fetchone()
    signals = conn.execute(
        "SELECT * FROM deck_signals WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    if not deck or not signals:
        return None
    score = conn.execute(
        "SELECT * FROM deck_scores WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    annotation = conn.execute(
        "SELECT * FROM deck_annotations WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()

    return {
        "fingerprint": fingerprint,
        "commander_name": deck["commander_name"],
        "source": deck["source"],
        "source_url": deck["source_url"],
        "author": deck["author"],
        "date_added": deck["date_added"],
        "cost": {
            "pct_owned": signals["pct_owned"],
            "usd_to_complete": signals["usd_to_complete"],
            "missing_no_price": signals["missing_no_price"],
        },
        "bracket_floor": {
            "floor": signals["bracket_floor"],
            "game_changer_count": signals["game_changer_count"],
            "has_mass_land_denial": bool(signals["has_mass_land_denial"]),
        },
        "combos": {
            "combo_count": signals["combo_count"],
            "top_combo_variant_id": signals["top_combo_variant_id"],
            "top_combo_pieces": signals["top_combo_pieces"],
            "top_combo_result": signals["top_combo_result"],
            "top_combo_relevance": signals["top_combo_relevance"],
            "median_assembly_turn": signals["median_assembly_turn"],
            "p25_assembly_turn": signals["p25_assembly_turn"],
            "mana_model_version": signals["mana_model_version"],
        },
        "feel_signals": {
            "tutor_count": signals["tutor_count"],
            "interaction_count": signals["interaction_count"],
            "avg_mana_value": signals["avg_mana_value"],
            "ramp_count": signals["ramp_count"],
            "fast_mana_count": signals["fast_mana_count"],
        },
        "score": {
            "declared_bracket_raw": score["declared_bracket_raw"],
            "declared_bracket_used": score["declared_bracket_used"],
            "label_conflict": bool(score["label_conflict"]),
            "feel_score": score["feel_score"],
            "feel_bracket": score["feel_bracket"],
            "confidence": score["confidence"],
            "low_confidence_reason": score["low_confidence_reason"],
        } if score else None,
        "feature_contributions": _feature_contributions(signals) if score else None,
        "annotation": {
            "starred": bool(annotation["starred"]),
            "rejected": bool(annotation["rejected"]),
            "notes": annotation["notes"],
        } if annotation else {"starred": False, "rejected": False, "notes": None},
    }


def explain_deck(conn: sqlite3.Connection, fingerprint: str) -> None:
    data = build_explanation(conn, fingerprint)
    if data is None:
        print(f"No deck found for fingerprint {fingerprint}")
        return

    print(f"=== {data['commander_name']} ({data['source']}) ===")
    print(f"URL: {data['source_url']}")
    print(f"Fingerprint: {fingerprint}")
    print()

    cost = data["cost"]
    print("-- Cost / ownership --")
    print(f"  {cost['pct_owned']:.0%} of non-basic cards already owned")
    print(f"  ${cost['usd_to_complete']:.2f} to complete (estimate; see limitations)")
    if cost["missing_no_price"]:
        print(f"  {cost['missing_no_price']} missing card(s) had no price data")
    print()

    floor = data["bracket_floor"]
    print("-- Rules-based bracket floor --")
    print(f"  floor = {floor['floor']}")
    print(f"  {floor['game_changer_count']} Game Changer(s)")
    print(f"  mass land denial: {'yes' if floor['has_mass_land_denial'] else 'no'}")
    print()

    combos = data["combos"]
    print("-- Combos --")
    print(f"  {combos['combo_count']} combo(s) detected against the local Spellbook variants dump")
    if combos["top_combo_variant_id"]:
        print(f"  top combo: {combos['top_combo_pieces']}-piece, "
              f"{combos['top_combo_result']}, relevance={combos['top_combo_relevance']:.3f}")
        print(f"  Monte Carlo assembly turn (model {combos['mana_model_version']}): "
              f"median={combos['median_assembly_turn']}, p25={combos['p25_assembly_turn']}")
    else:
        print("  no combo detected -- see known limitation #2 (zero-combo decks are low-confidence)")
    print()

    feel = data["feel_signals"]
    print("-- Other feel signals --")
    print(f"  tutors: {feel['tutor_count']}, interaction: {feel['interaction_count']}, "
          f"ramp: {feel['ramp_count']}, fast mana: {feel['fast_mana_count']}, "
          f"avg MV: {feel['avg_mana_value']}")
    print()

    score = data["score"]
    if score:
        print("-- Calibrated score --")
        declared = score["declared_bracket_raw"]
        print(f"  declared bracket: {declared if declared is not None else 'none (unlabeled source)'}"
              + (f"  [CONFLICT: below floor {floor['floor']}, "
                 f"corrected to {score['declared_bracket_used']} for training]"
                 if score["label_conflict"] else ""))
        print(f"  feel_score: {score['feel_score']:.2f}   feel_bracket: {score['feel_bracket']}   "
              f"confidence: {score['confidence']:.1%}")
        if score["low_confidence_reason"]:
            print(f"  LOW CONFIDENCE: {score['low_confidence_reason']}")
        print()

        if data["feature_contributions"]:
            print("-- Why: feature contributions per threshold model --")
            for entry in data["feature_contributions"]:
                print(f"  P(bracket >= {entry['threshold']}):")
                for c in entry["contributions"]:
                    note = "  (sentinel: no combo detected, not an actual turn count)" if c["sentinel"] else ""
                    print(f"    {c['feature']:<22} value={c['value']:<8.2f} "
                          f"coef={c['coefficient']:+.3f} contribution={c['contribution']:+.3f}{note}")
        else:
            print("  (no persisted model found -- run Stage 4 first)")
