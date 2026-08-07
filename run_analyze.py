"""Stage 3 entry point: compute cost, bracket floor, combos, Monte Carlo
assembly turn, and feel signals for every deck in the `decks` table,
writing the results to `deck_signals`. Independently re-runnable against
whatever Stage 2 already put in SQLite -- no network calls in this stage.
"""
from __future__ import annotations

import json
import sqlite3

from bracket_ranker.analyze.bracket_floor import BracketFloorInputs, compute_bracket_floor
from bracket_ranker.analyze.card_tags import refresh_all as refresh_card_tags
from bracket_ranker.analyze.combos import ComboMatch, build_combo_index, find_combos_in_deck
from bracket_ranker.analyze.cost import compute_cost
from bracket_ranker.analyze.feel_signals import compute_feel_signals
from bracket_ranker.analyze.mana_model.base import SimCard
from bracket_ranker.analyze.mana_model.v1_naive import NaiveManaModel
from bracket_ranker.config import EARLY_COMBO_TURN_CUTOFF, MONTE_CARLO_SIMULATIONS
from bracket_ranker.db import connect

MANA_MODEL = NaiveManaModel()


def _card_lookup(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT c.oracle_id, c.cmc, c.is_land, t.is_ramp, t.is_fast_mana
           FROM cards c JOIN card_tags t ON t.oracle_id = c.oracle_id"""
    ).fetchall()
    return {r["oracle_id"]: dict(r) for r in rows}


def _build_library(
    card_quantities: dict[str, int],
    commander_ids: set[str],
    card_lookup: dict[str, dict],
) -> list[SimCard]:
    library = []
    for oracle_id, qty in card_quantities.items():
        if oracle_id in commander_ids:
            continue  # commander lives in the command zone, not the library
        info = card_lookup.get(oracle_id)
        if info is None:
            continue
        for _ in range(qty):
            library.append(SimCard(
                oracle_id=oracle_id,
                cmc=info["cmc"] or 0.0,
                is_land=bool(info["is_land"]),
                is_ramp=bool(info["is_ramp"]),
                is_fast_mana=bool(info["is_fast_mana"]),
            ))
    return library


def _simulate(library: list[SimCard], combo: ComboMatch):
    return MANA_MODEL.simulate_assembly_turn(
        library, set(combo.oracle_ids), MONTE_CARLO_SIMULATIONS
    )


def analyze_deck(
    conn: sqlite3.Connection,
    deck_row: sqlite3.Row,
    combo_index,
    owned_oracle_ids: set[str],
    basic_land_ids: set[str],
    card_lookup: dict[str, dict],
) -> dict:
    fingerprint = deck_row["fingerprint"]
    card_rows = conn.execute(
        "SELECT oracle_id, quantity FROM deck_cards WHERE fingerprint = ?", (fingerprint,)
    ).fetchall()
    card_quantities = {r["oracle_id"]: r["quantity"] for r in card_rows}
    deck_oracle_ids = set(card_quantities)

    cost = compute_cost(conn, deck_oracle_ids, owned_oracle_ids, basic_land_ids)
    feel = compute_feel_signals(conn, deck_oracle_ids)

    matches = find_combos_in_deck(combo_index, deck_oracle_ids, feel.tutor_count)
    commander_ids = set((deck_row["commander_oracle_id"] or "").split(",")) - {""}

    top_combo = matches[0] if matches else None
    two_card_infinites = [m for m in matches if m.piece_count == 2 and m.is_infinite]

    median_turn = p25_turn = None
    mana_model_version = MANA_MODEL.VERSION
    if top_combo is not None:
        library = _build_library(card_quantities, commander_ids, card_lookup)
        result = _simulate(library, top_combo)
        median_turn, p25_turn = result.median_turn, result.p25_turn

    has_early_two_card_infinite = False
    if two_card_infinites:
        fastest = two_card_infinites[0]
        if top_combo is not None and fastest.variant_id == top_combo.variant_id:
            fastest_median = median_turn
        else:
            library = _build_library(card_quantities, commander_ids, card_lookup)
            fastest_median = _simulate(library, fastest).median_turn
        has_early_two_card_infinite = (
            fastest_median is not None and fastest_median <= EARLY_COMBO_TURN_CUTOFF
        )

    floor_result = compute_bracket_floor(BracketFloorInputs(
        game_changer_count=feel.game_changer_count,
        has_mass_land_denial=feel.has_mass_land_denial,
        has_early_two_card_infinite=has_early_two_card_infinite,
    ))

    return {
        "fingerprint": fingerprint,
        "pct_owned": cost.pct_owned,
        "usd_to_complete": cost.usd_to_complete,
        "missing_no_price": cost.missing_no_price,
        "game_changer_count": feel.game_changer_count,
        "has_mass_land_denial": int(feel.has_mass_land_denial),
        "bracket_floor": floor_result.floor,
        "combo_count": len(matches),
        "top_combo_variant_id": top_combo.variant_id if top_combo else None,
        "top_combo_pieces": top_combo.piece_count if top_combo else None,
        "top_combo_result": "game-ending" if (top_combo and top_combo.is_game_ender) else
                             ("infinite" if (top_combo and top_combo.is_infinite) else
                              ("value" if top_combo else None)),
        "top_combo_relevance": top_combo.relevance if top_combo else None,
        "median_assembly_turn": median_turn,
        "p25_assembly_turn": p25_turn,
        "mana_model_version": mana_model_version,
        "tutor_count": feel.tutor_count,
        "interaction_count": feel.interaction_count,
        "avg_mana_value": feel.avg_mana_value,
        "ramp_count": feel.ramp_count,
        "fast_mana_count": feel.fast_mana_count,
    }


def refresh_all() -> None:
    with connect() as conn:
        print("[analyze] refreshing card_tags...")
        refresh_card_tags(conn)

        owned_oracle_ids = {r[0] for r in conn.execute("SELECT oracle_id FROM collection")}
        basic_land_ids = {r[0] for r in conn.execute("SELECT oracle_id FROM cards WHERE is_basic_land = 1")}
        card_lookup = _card_lookup(conn)

        print("[analyze] building combo index...")
        combo_index = build_combo_index(conn)

        deck_rows = conn.execute("SELECT * FROM decks").fetchall()
        print(f"[analyze] analyzing {len(deck_rows)} decks...")

        results = []
        for i, deck_row in enumerate(deck_rows, 1):
            results.append(analyze_deck(
                conn, deck_row, combo_index, owned_oracle_ids, basic_land_ids, card_lookup
            ))
            if i % 100 == 0:
                print(f"\r[analyze] {i}/{len(deck_rows)}...", end="", flush=True)
        print()

        conn.execute("DELETE FROM deck_signals")
        conn.executemany(
            """INSERT INTO deck_signals (
                fingerprint, pct_owned, usd_to_complete, missing_no_price,
                game_changer_count, has_mass_land_denial, bracket_floor,
                combo_count, top_combo_variant_id, top_combo_pieces, top_combo_result,
                top_combo_relevance, median_assembly_turn, p25_assembly_turn,
                mana_model_version, tutor_count, interaction_count, avg_mana_value,
                ramp_count, fast_mana_count
            ) VALUES (
                :fingerprint, :pct_owned, :usd_to_complete, :missing_no_price,
                :game_changer_count, :has_mass_land_denial, :bracket_floor,
                :combo_count, :top_combo_variant_id, :top_combo_pieces, :top_combo_result,
                :top_combo_relevance, :median_assembly_turn, :p25_assembly_turn,
                :mana_model_version, :tutor_count, :interaction_count, :avg_mana_value,
                :ramp_count, :fast_mana_count
            )""",
            results,
        )
    print(f"[analyze] wrote deck_signals for {len(results)} decks")


if __name__ == "__main__":
    refresh_all()
