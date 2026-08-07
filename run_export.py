"""Stage 5.1: the main ranking CSV, sorted by cost to complete ascending.

Independently re-runnable against whatever Stage 3/4 already wrote to
SQLite -- no computation happens here, just a join and a sort.
"""
from __future__ import annotations

import csv
from collections import Counter

from bracket_ranker.config import REPORTS_DIR
from bracket_ranker.db import connect
from bracket_ranker.rank.limitations import print_limitations

OUTPUT_PATH = REPORTS_DIR / "ranking.csv"

COLUMNS = [
    "commander", "source", "source_url", "deck_fingerprint", "pct_owned", "usd_to_complete",
    "game_changer_count", "has_mass_land_denial", "bracket_floor",
    "combo_count", "top_combo_pieces", "top_combo_result", "median_assembly_turn", "p25_assembly_turn",
    "tutor_count", "interaction_count", "avg_mana_value", "ramp_count", "fast_mana_count",
    "declared_bracket", "feel_score", "feel_bracket", "confidence",
]

QUERY = """
SELECT
    d.commander_name AS commander, d.source, d.source_url, d.fingerprint AS deck_fingerprint,
    s.pct_owned, s.usd_to_complete,
    s.game_changer_count, s.has_mass_land_denial, s.bracket_floor,
    s.combo_count, s.top_combo_pieces, s.top_combo_result,
    s.median_assembly_turn, s.p25_assembly_turn,
    s.tutor_count, s.interaction_count, s.avg_mana_value, s.ramp_count, s.fast_mana_count,
    sc.declared_bracket_raw AS declared_bracket, sc.feel_score, sc.feel_bracket, sc.confidence
FROM decks d
JOIN deck_signals s ON s.fingerprint = d.fingerprint
LEFT JOIN deck_scores sc ON sc.fingerprint = d.fingerprint
ORDER BY s.usd_to_complete ASC
"""


def refresh_all() -> None:
    with connect() as conn:
        rows = conn.execute(QUERY).fetchall()
        source_counts = Counter(r["source"] for r in rows)

        with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for row in rows:
                writer.writerow([row[c] for c in COLUMNS])

    print(f"[export] wrote {len(rows)} decks to {OUTPUT_PATH}")
    print("[export] corpus size and per-source breakdown "
          "(a large biased sample, not a census -- see limitations below):")
    for source, count in source_counts.most_common():
        print(f"    {source}: {count}")
    print()
    print_limitations()


if __name__ == "__main__":
    refresh_all()
