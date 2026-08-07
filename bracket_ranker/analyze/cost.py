"""Stage 3.1: cost to complete a deck against the owned collection.

missing = deck_oracle_ids - owned_oracle_ids, summed at each missing card's
cheapest known printing price (usd_min, built in scryfall.py from the
*whole* printings table -- not whatever single printing a bulk "oracle
card" row happened to carry). Basic lands are excluded on both sides of the
set difference, per the spec: nobody needs to "complete" a deck's Forests.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class CostResult:
    pct_owned: float
    usd_to_complete: float
    missing_no_price: int  # missing cards where even usd_min is null


def compute_cost(
    conn: sqlite3.Connection,
    deck_oracle_ids: set[str],
    owned_oracle_ids: set[str],
    basic_land_ids: set[str],
) -> CostResult:
    relevant = deck_oracle_ids - basic_land_ids
    if not relevant:
        return CostResult(pct_owned=1.0, usd_to_complete=0.0, missing_no_price=0)

    owned_relevant = relevant & owned_oracle_ids
    missing = relevant - owned_oracle_ids

    usd_to_complete = 0.0
    missing_no_price = 0
    if missing:
        placeholders = ",".join("?" * len(missing))
        rows = conn.execute(
            f"SELECT oracle_id, usd_min, usd_min_foil FROM cards WHERE oracle_id IN ({placeholders})",
            list(missing),
        ).fetchall()
        priced = {row["oracle_id"]: row for row in rows}
        for oracle_id in missing:
            row = priced.get(oracle_id)
            price = None
            if row:
                price = row["usd_min"] if row["usd_min"] is not None else row["usd_min_foil"]
            if price is None:
                missing_no_price += 1
            else:
                usd_to_complete += price

    return CostResult(
        pct_owned=len(owned_relevant) / len(relevant),
        usd_to_complete=round(usd_to_complete, 2),
        missing_no_price=missing_no_price,
    )
