"""Build a deck's simulation inputs (a list of SimCards, color-identity-
aware) from SQLite. Originally lived as private helpers inside
run_analyze.py; promoted to a shared module once the web UI's on-demand
stress test and table simulation needed the exact same logic Stage 3
already used -- one implementation, not two copies that could drift.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from bracket_ranker.analyze.mana_model.base import SimCard


def card_lookup(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT c.oracle_id, c.name, c.cmc, c.is_land, c.mana_cost, c.produced_mana, c.color_identity,
                  t.is_ramp, t.is_fast_mana
           FROM cards c JOIN card_tags t ON t.oracle_id = c.oracle_id"""
    ).fetchall()
    lookup = {}
    for r in rows:
        info = dict(r)
        info["produced_mana"] = json.loads(info["produced_mana"]) if info["produced_mana"] else []
        info["color_identity"] = json.loads(info["color_identity"]) if info["color_identity"] else []
        lookup[r["oracle_id"]] = info
    return lookup


def deck_color_identity(card_quantities: dict[str, int], lookup: dict[str, dict]) -> set[str]:
    identity: set[str] = set()
    for oracle_id in card_quantities:
        info = lookup.get(oracle_id)
        if info:
            identity.update(info["color_identity"])
    return identity


def build_library(
    card_quantities: dict[str, int],
    commander_ids: set[str],
    lookup: dict[str, dict],
) -> list[SimCard]:
    # Identity-dependent fixers (Arcane Signet, Chromatic Lantern: "any
    # color in your commander's color identity") list all five colors in
    # Scryfall's own produced_mana regardless of deck -- verified live --
    # so without this filter they'd look like universal 5-color fixers
    # even in a mono-color deck. Colorless ('C') sources are never
    # identity-restricted, so they pass through unfiltered.
    identity = deck_color_identity(card_quantities, lookup)

    library = []
    for oracle_id, qty in card_quantities.items():
        if oracle_id in commander_ids:
            continue  # commander lives in the command zone, not the library
        info = lookup.get(oracle_id)
        if info is None:
            continue
        produced = tuple(c for c in info["produced_mana"] if c in identity or c == "C")
        for _ in range(qty):
            library.append(SimCard(
                oracle_id=oracle_id,
                cmc=info["cmc"] or 0.0,
                is_land=bool(info["is_land"]),
                is_ramp=bool(info["is_ramp"]),
                is_fast_mana=bool(info["is_fast_mana"]),
                mana_cost=info["mana_cost"] or "",
                produced_mana=produced,
            ))
    return library


def build_deck_simulation_inputs(
    conn: sqlite3.Connection,
    fingerprint: str,
    lookup: dict[str, dict],
) -> tuple[list[SimCard], dict[str, int]] | None:
    """Returns (library, card_quantities) for a deck, or None if it's not
    in the decks table. card_quantities is returned too since callers
    (combo detection) need the raw oracle_id set independent of the
    library's per-copy expansion."""
    deck = conn.execute("SELECT * FROM decks WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if deck is None:
        return None
    card_rows = conn.execute(
        "SELECT oracle_id, quantity FROM deck_cards WHERE fingerprint = ?", (fingerprint,)
    ).fetchall()
    card_quantities = {r["oracle_id"]: r["quantity"] for r in card_rows}
    commander_ids = set((deck["commander_oracle_id"] or "").split(",")) - {""}
    library = build_library(card_quantities, commander_ids, lookup)
    return library, card_quantities


@dataclass
class DeckSimulationInputs:
    fingerprint: str
    commander_name: str
    source_url: str
    library: list[SimCard]
    combo_targets: list  # list[ComboTarget], typed loosely to avoid a stress_test<->deck_library import cycle
    interaction_count: int


def build_full_simulation_inputs(
    conn: sqlite3.Connection,
    fingerprint: str,
    lookup: dict[str, dict],
    combo_index,
    max_combo_targets: int = 8,
) -> DeckSimulationInputs | None:
    """Everything the stress test / table simulation need for one deck, in
    one call: the color-aware library, its detected combos (as
    stress_test.ComboTarget, imported lazily below to avoid a circular
    import -- deck_library is the lower-level module combos/stress_test
    both sit above), and its interaction density for the table-sim
    disruption heuristic.
    """
    from bracket_ranker.analyze.combos import find_combos_in_deck
    from bracket_ranker.analyze.feel_signals import compute_feel_signals
    from bracket_ranker.analyze.stress_test import ComboTarget

    deck = conn.execute("SELECT * FROM decks WHERE fingerprint = ?", (fingerprint,)).fetchone()
    result = build_deck_simulation_inputs(conn, fingerprint, lookup)
    if deck is None or result is None:
        return None
    library, card_quantities = result
    deck_oracle_ids = set(card_quantities)

    feel = compute_feel_signals(conn, deck_oracle_ids)
    matches = find_combos_in_deck(combo_index, deck_oracle_ids, feel.tutor_count)
    combo_targets = [
        ComboTarget(
            m.variant_id, m.oracle_ids, m.piece_count, m.is_game_ender, m.is_infinite,
            card_names=tuple(sorted(
                lookup[oid]["name"] for oid in m.oracle_ids if oid in lookup
            )),
        )
        for m in matches[:max_combo_targets]
    ]

    return DeckSimulationInputs(
        fingerprint=fingerprint,
        commander_name=deck["commander_name"],
        source_url=deck["source_url"],
        library=library,
        combo_targets=combo_targets,
        interaction_count=feel.interaction_count,
    )
