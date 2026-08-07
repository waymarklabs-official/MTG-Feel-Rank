"""Spec 3.1: cross-check our Scryfall-derived usd_to_complete against
Archidekt's own price field.

Only meaningful for Archidekt-sourced decks -- that's the only source where
we captured a comparable deck-level price (source_price_usd, summed from
each card's TCGPlayer price at ingest time, see ingest/archidekt.py). The
two numbers are NOT expected to match: ours is "cost of what's missing from
one specific collection", Archidekt's is "cost of the whole deck" -- so
this reports the ratio of (our missing-card total) to (Archidekt's whole
-deck total) as a plausibility check, not an equality check. A ratio badly
above 1.0 (we think completing costs more than the whole deck) or a huge
spread would indicate a real pricing bug; a wide but sub-1.0 spread is
expected and healthy (it just reflects how much of each deck you already own).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class PriceCrosscheckRow:
    fingerprint: str
    commander_name: str
    our_usd_to_complete: float
    archidekt_price_usd: float
    ratio: float  # our_usd_to_complete / archidekt_price_usd


def run_price_crosscheck(conn: sqlite3.Connection) -> list[PriceCrosscheckRow]:
    rows = conn.execute(
        """
        SELECT d.fingerprint, d.commander_name, d.source_price_usd, s.usd_to_complete
        FROM decks d
        JOIN deck_signals s ON s.fingerprint = d.fingerprint
        WHERE d.source = 'archidekt' AND d.source_price_usd IS NOT NULL AND d.source_price_usd > 0
        """
    ).fetchall()
    out = []
    for row in rows:
        ratio = row["usd_to_complete"] / row["source_price_usd"]
        out.append(PriceCrosscheckRow(
            fingerprint=row["fingerprint"],
            commander_name=row["commander_name"],
            our_usd_to_complete=row["usd_to_complete"],
            archidekt_price_usd=row["source_price_usd"],
            ratio=ratio,
        ))
    return out


def report_price_crosscheck(rows: list[PriceCrosscheckRow]) -> None:
    if not rows:
        print("[price_crosscheck] no Archidekt decks with price data to compare")
        return
    ratios = sorted(r.ratio for r in rows)
    median_ratio = ratios[len(ratios) // 2]
    over_one = sum(1 for r in ratios if r > 1.0)
    print(f"[price_crosscheck] {len(rows)} Archidekt decks compared")
    print(f"[price_crosscheck] median (our missing-card cost / their whole-deck cost) = "
          f"{median_ratio:.2f}")
    print(f"[price_crosscheck] {over_one} decks ({over_one / len(rows):.1%}) where our "
          f"'missing cards' estimate exceeds Archidekt's whole-deck price -- "
          f"a real red flag if this is more than a handful (should only ever happen when "
          f"Archidekt's own price data is stale/incomplete for that deck)")


def store_price_crosscheck(conn: sqlite3.Connection, rows: list[PriceCrosscheckRow]) -> None:
    conn.execute("DELETE FROM archidekt_price_crosscheck")
    conn.executemany(
        """INSERT INTO archidekt_price_crosscheck (
            fingerprint, our_usd_to_complete, archidekt_price_usd, ratio
        ) VALUES (?,?,?,?)""",
        [(r.fingerprint, r.our_usd_to_complete, r.archidekt_price_usd, r.ratio) for r in rows],
    )


if __name__ == "__main__":
    from bracket_ranker.db import connect
    with connect() as conn:
        rows = run_price_crosscheck(conn)
        store_price_crosscheck(conn, rows)
    report_price_crosscheck(rows)
