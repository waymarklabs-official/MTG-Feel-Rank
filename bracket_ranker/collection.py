"""Parse ManaBox_Collection.csv into owned oracle_ids + quantities.

ManaBox already exports a per-row "Scryfall ID" -- that's a *printing* id
(one specific set/foil/language), not an oracle_id. We resolve it via the
`printings` table built in scryfall.py. Only if that fails (e.g. a printing
too new for our cached bulk data) do we fall back to name matching, and only
as a last resort do we hit the live Scryfall API -- exactly the "bulk for
everything, API calls only for stragglers" rule from the spec.

Basic lands are intentionally NOT filtered out here: this module's job is
"what does the user actually own", full stop. Basic-land exclusion happens
downstream in Stage 3, where a deck's needs are compared against this
collection -- that's where "a deck doesn't need Forests bought for it"
actually matters, not here.
"""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

import requests

from bracket_ranker.config import MANABOX_CSV_PATH, USER_AGENT
from bracket_ranker.db import connect
from bracket_ranker.scryfall import build_name_index, normalize_name

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


@dataclass
class UnresolvedRow:
    row_number: int
    name: str
    set_code: str
    scryfall_id: str
    reason: str


@dataclass
class CollectionResult:
    owned: dict[str, int] = field(default_factory=dict)  # oracle_id -> quantity
    unresolved: list[UnresolvedRow] = field(default_factory=list)
    resolved_by_id: int = 0
    resolved_by_name: int = 0
    resolved_by_api: int = 0


def _lookup_scryfall_by_id_live(scryfall_id: str) -> str | None:
    """Last-resort single-card API call for a printing our bulk cache
    doesn't know about yet (e.g. released after our last refresh)."""
    try:
        resp = requests.get(
            f"https://api.scryfall.com/cards/{scryfall_id}",
            headers=HEADERS, timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("oracle_id")
    except requests.RequestException:
        pass
    return None


def parse_manabox_csv(conn: sqlite3.Connection) -> CollectionResult:
    printing_map: dict[str, str] = {
        row[0]: row[1] for row in conn.execute("SELECT scryfall_id, oracle_id FROM printings")
    }
    name_index = build_name_index(conn)
    all_names = [row[0] for row in conn.execute("SELECT name FROM cards")]

    result = CollectionResult()
    quantities: dict[str, int] = defaultdict(int)

    with open(MANABOX_CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Fail loudly if ManaBox ever renames a column we depend on, rather
        # than silently mis-parsing every row.
        required = {"Name", "Set code", "Scryfall ID", "Quantity"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"ManaBox_Collection.csv is missing expected column(s): {missing}. "
                f"Actual headers: {reader.fieldnames}"
            )

        for row_number, row in enumerate(reader, start=2):  # header is row 1
            quantity = int(row["Quantity"] or 1)
            scryfall_id = (row.get("Scryfall ID") or "").strip()
            name = (row.get("Name") or "").strip()

            oracle_id = printing_map.get(scryfall_id) if scryfall_id else None
            if oracle_id:
                result.resolved_by_id += 1
            else:
                oracle_id = name_index.get(name) or name_index.get(normalize_name(name))
                if oracle_id:
                    result.resolved_by_name += 1
                elif scryfall_id:
                    oracle_id = _lookup_scryfall_by_id_live(scryfall_id)
                    if oracle_id:
                        result.resolved_by_api += 1

            if oracle_id:
                quantities[oracle_id] += quantity
            else:
                from difflib import get_close_matches
                suggestions = get_close_matches(name, all_names, n=3, cutoff=0.6)
                result.unresolved.append(UnresolvedRow(
                    row_number=row_number,
                    name=name,
                    set_code=row.get("Set code", ""),
                    scryfall_id=scryfall_id,
                    reason=f"no match; closest names: {suggestions}" if suggestions
                           else "no match; no close name suggestions",
                ))

    result.owned = dict(quantities)
    return result


def store_collection(conn: sqlite3.Connection, owned: dict[str, int]) -> None:
    conn.execute("DELETE FROM collection")
    conn.executemany(
        "INSERT INTO collection (oracle_id, quantity) VALUES (?, ?)",
        list(owned.items()),
    )


def write_unresolved_report(unresolved: list[UnresolvedRow]) -> None:
    from bracket_ranker.config import REPORTS_DIR
    path = REPORTS_DIR / "collection_unresolved.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["row_number", "name", "set_code", "scryfall_id", "reason"])
        for u in unresolved:
            writer.writerow([u.row_number, u.name, u.set_code, u.scryfall_id, u.reason])
    print(f"[collection] wrote {len(unresolved)} unresolved rows to {path}")


def refresh_all() -> CollectionResult:
    with connect() as conn:
        result = parse_manabox_csv(conn)
        store_collection(conn, result.owned)
    write_unresolved_report(result.unresolved)
    print(
        f"[collection] {len(result.owned)} distinct owned cards "
        f"(resolved: {result.resolved_by_id} by printing id, "
        f"{result.resolved_by_name} by name, {result.resolved_by_api} by live API "
        f"lookup; {len(result.unresolved)} unresolved)"
    )
    return result


if __name__ == "__main__":
    refresh_all()
