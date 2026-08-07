"""Stage 5.2: the query interface -- answers the question the user
actually has ("find me a deck for commander X, bracket Y, under $Z").
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class QueryFilters:
    commander: str | None = None
    bracket: int | None = None
    max_cost: float | None = None
    min_confidence: float = 0.0
    top_n: int = 10


QUERY = """
SELECT
    d.commander_name, d.source, d.source_url, d.fingerprint,
    s.pct_owned, s.usd_to_complete, s.bracket_floor, s.combo_count,
    s.top_combo_pieces, s.top_combo_result, s.median_assembly_turn,
    s.game_changer_count, s.has_mass_land_denial,
    s.tutor_count, s.interaction_count, s.avg_mana_value,
    sc.feel_score, sc.feel_bracket, sc.confidence, sc.low_confidence_reason
FROM decks d
JOIN deck_signals s ON s.fingerprint = d.fingerprint
JOIN deck_scores sc ON sc.fingerprint = d.fingerprint
WHERE (:commander IS NULL OR d.commander_name LIKE :commander_like)
  AND (:bracket IS NULL OR sc.feel_bracket = :bracket)
  AND (:max_cost IS NULL OR s.usd_to_complete <= :max_cost)
  AND sc.confidence >= :min_confidence
ORDER BY s.usd_to_complete ASC
LIMIT :top_n
"""


def run_query(conn: sqlite3.Connection, filters: QueryFilters) -> list[sqlite3.Row]:
    return conn.execute(QUERY, {
        "commander": filters.commander,
        "commander_like": f"%{filters.commander}%" if filters.commander else None,
        "bracket": filters.bracket,
        "max_cost": filters.max_cost,
        "min_confidence": filters.min_confidence,
        "top_n": filters.top_n,
    }).fetchall()


def explain_one_line(row: sqlite3.Row) -> str:
    signals = []
    if row["game_changer_count"]:
        signals.append(f"{row['game_changer_count']} Game Changers")
    if row["has_mass_land_denial"]:
        signals.append("mass land denial")
    if row["combo_count"]:
        turn_desc = (f"assembles turn {row['median_assembly_turn']:.0f}"
                     if row["median_assembly_turn"] is not None
                     else "didn't assemble within the simulation horizon in most simulated games")
        signals.append(f"top combo: {row['top_combo_pieces']}pc {row['top_combo_result']} ({turn_desc})")
    else:
        signals.append("no combo detected")
    if row["tutor_count"]:
        signals.append(f"{row['tutor_count']} tutors")
    if row["interaction_count"]:
        signals.append(f"{row['interaction_count']} interaction")
    low_conf = f" [LOW CONFIDENCE: {row['low_confidence_reason']}]" if row["low_confidence_reason"] else ""
    return (
        f"{row['commander_name']} [{row['source']}] "
        f"${row['usd_to_complete']:.2f} to complete, {row['pct_owned']:.0%} owned, "
        f"feel_bracket={row['feel_bracket']} (score={row['feel_score']:.2f}, "
        f"confidence={row['confidence']:.0%}){low_conf}\n"
        f"    driven by: {', '.join(signals)}\n"
        f"    {row['source_url']}"
    )
