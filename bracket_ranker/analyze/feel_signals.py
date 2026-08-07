"""Stage 3.5: the remaining "feel" signals -- tutor/interaction density,
average mana value, ramp count, fast mana count.

All straightforward aggregations over deck_cards joined against the
card_tags computed in card_tags.py and cmc from the cards table. Basic
lands are naturally excluded from avg_mana_value by SQL (a land's cmc is
always 0, which would silently drag the average down) -- we filter on
`is_land = 0` rather than `is_basic_land = 0` for that one signal, since a
Command Tower's 0 mana value shouldn't count toward "how expensive is this
deck's non-land spells" any more than a Forest's should.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class FeelSignals:
    tutor_count: int
    interaction_count: int
    avg_mana_value: float
    ramp_count: int
    fast_mana_count: int
    game_changer_count: int
    has_mass_land_denial: bool


def compute_feel_signals(conn: sqlite3.Connection, deck_oracle_ids: set[str]) -> FeelSignals:
    if not deck_oracle_ids:
        return FeelSignals(0, 0, 0.0, 0, 0, 0, False)

    placeholders = ",".join("?" * len(deck_oracle_ids))
    ids = list(deck_oracle_ids)

    row = conn.execute(
        f"""
        SELECT
            COALESCE(SUM(t.is_tutor), 0) AS tutor_count,
            COALESCE(SUM(t.is_interaction), 0) AS interaction_count,
            COALESCE(SUM(t.is_ramp), 0) AS ramp_count,
            COALESCE(SUM(t.is_fast_mana), 0) AS fast_mana_count,
            COALESCE(SUM(t.is_mass_land_denial), 0) AS mld_count,
            COALESCE(SUM(c.game_changer), 0) AS game_changer_count
        FROM cards c
        JOIN card_tags t ON t.oracle_id = c.oracle_id
        WHERE c.oracle_id IN ({placeholders})
        """,
        ids,
    ).fetchone()

    mv_row = conn.execute(
        f"""
        SELECT AVG(cmc) AS avg_cmc FROM cards
        WHERE oracle_id IN ({placeholders}) AND is_land = 0
        """,
        ids,
    ).fetchone()

    return FeelSignals(
        tutor_count=row["tutor_count"],
        interaction_count=row["interaction_count"],
        avg_mana_value=round(mv_row["avg_cmc"], 2) if mv_row["avg_cmc"] is not None else 0.0,
        ramp_count=row["ramp_count"],
        fast_mana_count=row["fast_mana_count"],
        game_changer_count=row["game_changer_count"],
        has_mass_land_denial=row["mld_count"] > 0,
    )
