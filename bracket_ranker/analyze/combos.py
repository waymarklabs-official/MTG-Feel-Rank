"""Stage 3.3: combo detection via local set-intersection against the
Spellbook variants dump (never per-deck API calls -- see spellbook.py).

A combo is "present" in a deck if every one of its oracle_ids is a subset
of the deck's oracle_id set. With ~40k combos and thousands of decks, a
naive decks x combos double loop is wasteful; instead we build an inverted
index (oracle_id -> combos that use it) once, so each deck only has to
subset-check the combos that share at least one card with it.

Relevance scoring answers "which detected combo, if any, is the deck
actually built around" -- most detected combos are incidental two-card
interactions nobody is playing toward (774 for Prosper alone, per the
spec). Weighted by fewer pieces, a game-ending result, and how much tutor
support the deck has to actually find the pieces. Weights are named
constants specifically so they're easy to tune from outside this module.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

# Tunable relevance weights (spec: "expose a threshold I can tune").
RELEVANCE_PIECE_WEIGHT = 1.0     # 1 / piece_count
RELEVANCE_GAME_ENDER_MULTIPLIER = 2.0
RELEVANCE_TUTOR_BONUS_PER_CARD = 0.1
RELEVANCE_TUTOR_BONUS_CAP = 5


@dataclass
class ComboMatch:
    variant_id: str
    oracle_ids: frozenset[str]
    piece_count: int
    is_game_ender: bool
    is_infinite: bool
    relevance: float


@dataclass
class ComboIndex:
    by_card: dict[str, list[tuple[str, frozenset[str], int, bool, bool]]]
    # oracle_id -> list of (variant_id, oracle_ids, piece_count, is_game_ender, is_infinite)


def build_combo_index(conn: sqlite3.Connection) -> ComboIndex:
    by_card: dict[str, list] = {}
    for row in conn.execute(
        "SELECT variant_id, oracle_ids, piece_count, is_game_ender, is_infinite FROM combos"
    ):
        ids = frozenset(json.loads(row["oracle_ids"]))
        entry = (row["variant_id"], ids, row["piece_count"],
                 bool(row["is_game_ender"]), bool(row["is_infinite"]))
        for oracle_id in ids:
            by_card.setdefault(oracle_id, []).append(entry)
    return ComboIndex(by_card=by_card)


def relevance_score(piece_count: int, is_game_ender: bool, deck_tutor_count: int) -> float:
    score = RELEVANCE_PIECE_WEIGHT / piece_count
    if is_game_ender:
        score *= RELEVANCE_GAME_ENDER_MULTIPLIER
    score *= 1.0 + RELEVANCE_TUTOR_BONUS_PER_CARD * min(deck_tutor_count, RELEVANCE_TUTOR_BONUS_CAP)
    return score


def find_combos_in_deck(
    index: ComboIndex,
    deck_oracle_ids: set[str],
    deck_tutor_count: int,
) -> list[ComboMatch]:
    seen_variant_ids: set[str] = set()
    matches: list[ComboMatch] = []
    for oracle_id in deck_oracle_ids:
        for variant_id, combo_ids, piece_count, is_game_ender, is_infinite in index.by_card.get(oracle_id, []):
            if variant_id in seen_variant_ids:
                continue
            seen_variant_ids.add(variant_id)
            if combo_ids <= deck_oracle_ids:  # subset check: all pieces present
                matches.append(ComboMatch(
                    variant_id=variant_id,
                    oracle_ids=combo_ids,
                    piece_count=piece_count,
                    is_game_ender=is_game_ender,
                    is_infinite=is_infinite,
                    relevance=relevance_score(piece_count, is_game_ender, deck_tutor_count),
                ))
    matches.sort(key=lambda m: -m.relevance)
    return matches
