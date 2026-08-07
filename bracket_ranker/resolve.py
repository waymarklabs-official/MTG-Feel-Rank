"""Stage 2: resolve every deck's cards to oracle_ids, fingerprint, dedupe,
and store in SQLite.

This is the stage where the spec's central design decision -- "never key
anything on card names" -- actually gets enforced. Archidekt, MTGJSON, and
EDHTop16 already hand us an oracle_id per card (verified live in Stage 1);
those are trusted only after confirming they exist in our own `cards` table
(a source's oracle_id could point at a printing our Scryfall cache doesn't
know about yet). Anything without a source-provided oracle_id -- or whose
oracle_id doesn't check out -- falls back to the name index, then to
difflib suggestions in the unresolved report. Never a silent guess.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import get_close_matches

from bracket_ranker.config import DECK_RECORDS_PATH, REPORTS_DIR
from bracket_ranker.db import connect
from bracket_ranker.ingest.base import DeckRecord
from bracket_ranker.ingest.serialize import read_deck_records
from bracket_ranker.scryfall import build_name_index, normalize_name


@dataclass
class UnresolvedCard:
    source: str
    source_deck_id: str
    commander_name: str
    card_name: str
    reason: str


@dataclass
class ResolvedDeck:
    fingerprint: str
    commander_name: str
    commander_oracle_id: str | None
    source: str
    source_url: str
    source_deck_id: str
    declared_bracket: int | None
    source_price_usd: float | None
    date_added: str | None
    author: str | None
    raw_metadata: dict
    card_quantities: dict[str, int]  # oracle_id -> quantity, basics excluded from fingerprint only

    # Sources whose label is asserted as ground truth by the spec (precons
    # are BY DEFINITION Bracket 2; EDHTop16 decks are BY DEFINITION cEDH)
    # outrank a self-reported Archidekt declaration of the same decklist.
    # Without this, a precon re-uploaded to Archidekt and self-declared
    # (say) Bracket 3 would silently steal the fingerprint slot -- verified
    # live: source_price_usd is always populated for Archidekt but never
    # for MTGJSON precons, so the naive "more metadata wins" tie-break
    # handed every re-uploaded precon's dedup slot to Archidekt, corrupting
    # the one label the spec calls unconditionally trustworthy.
    _GROUND_TRUTH_SOURCES = {"mtgjson_precon", "edhtop16"}

    def richness_score(self) -> tuple:
        return (
            1 if self.source in self._GROUND_TRUTH_SOURCES else 0,
            1 if self.declared_bracket is not None else 0,
            1 if self.source_price_usd is not None else 0,
            len(self.card_quantities),
        )


def _resolve_name(name: str, name_index: dict[str, str], all_names: list[str],
                   unresolved: list[UnresolvedCard], source: str, source_deck_id: str,
                   commander_name: str) -> str | None:
    oracle_id = name_index.get(name) or name_index.get(normalize_name(name))
    if oracle_id:
        return oracle_id
    suggestions = get_close_matches(name, all_names, n=3, cutoff=0.6)
    unresolved.append(UnresolvedCard(
        source=source, source_deck_id=source_deck_id, commander_name=commander_name,
        card_name=name,
        reason=f"no match; closest names: {suggestions}" if suggestions
               else "no match; no close name suggestions",
    ))
    return None


def _fingerprint(oracle_ids: set[str]) -> str:
    joined = "\n".join(sorted(oracle_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def resolve_deck_records(
    conn: sqlite3.Connection,
    records: list[DeckRecord],
) -> tuple[list[ResolvedDeck], list[UnresolvedCard]]:
    known_oracle_ids = {row[0] for row in conn.execute("SELECT oracle_id FROM cards")}
    basic_land_ids = {row[0] for row in conn.execute(
        "SELECT oracle_id FROM cards WHERE is_basic_land = 1"
    )}
    name_index = build_name_index(conn)
    all_names = [row[0] for row in conn.execute("SELECT name FROM cards")]

    resolved: list[ResolvedDeck] = []
    unresolved: list[UnresolvedCard] = []

    for record in records:
        quantities: dict[str, int] = defaultdict(int)
        for card in record.cards:
            oracle_id = card.oracle_id if card.oracle_id in known_oracle_ids else None
            if oracle_id is None:
                oracle_id = _resolve_name(
                    card.name, name_index, all_names, unresolved,
                    record.source, record.source_deck_id, record.commander_name,
                )
            if oracle_id:
                quantities[oracle_id] += card.quantity

        if not quantities:
            continue  # nothing resolved at all -- not a usable deck

        # Commanders are resolved the same way, joined for partner pairs.
        # We don't require this to succeed to keep the deck (a deck's
        # 99 spells are still useful corpus/cost data even if a weird
        # commander name fails to resolve), but it IS required for a deck
        # to serve as a labeled example of "how does commander X play".
        commander_oracle_ids = []
        for name in record.commander_name.split(" + "):
            oid = name_index.get(name) or name_index.get(normalize_name(name))
            if oid:
                commander_oracle_ids.append(oid)

        fingerprint_ids = set(quantities) - basic_land_ids
        if not fingerprint_ids:
            continue  # a "deck" that's entirely basic lands isn't a deck

        resolved.append(ResolvedDeck(
            fingerprint=_fingerprint(fingerprint_ids),
            commander_name=record.commander_name,
            commander_oracle_id=",".join(commander_oracle_ids) or None,
            source=record.source,
            source_url=record.source_url,
            source_deck_id=record.source_deck_id,
            declared_bracket=record.declared_bracket,
            source_price_usd=record.source_price_usd,
            date_added=record.date_added,
            author=record.author,
            raw_metadata=record.raw_metadata,
            card_quantities=dict(quantities),
        ))

    return resolved, unresolved


def dedupe_decks(decks: list[ResolvedDeck]) -> list[ResolvedDeck]:
    best: dict[str, ResolvedDeck] = {}
    for deck in decks:
        current = best.get(deck.fingerprint)
        if current is None or deck.richness_score() > current.richness_score():
            best[deck.fingerprint] = deck
    return list(best.values())


def _tables_referencing(conn: sqlite3.Connection, parent_table: str) -> list[str]:
    """Every table with a FOREIGN KEY pointing at parent_table, found by
    reading the schema rather than hand-listing them -- new tables that
    reference decks(fingerprint) (there have already been several: deck_
    cards, deck_signals, deck_scores, the two cross-check tables, deck_
    annotations) get picked up automatically instead of silently causing
    the same FK-constraint failure again the next time one is added."""
    referencing = []
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table = row[0]
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
            if fk[2] == parent_table:  # fk[2] is the referenced table name
                referencing.append(table)
                break
    return referencing


def store_decks(conn: sqlite3.Connection, decks: list[ResolvedDeck]) -> None:
    # Fingerprints can legitimately disappear or change dedup winner
    # between runs; every table keyed on fingerprint gets recomputed
    # wholesale by its own stage anyway, so clearing them here is free.
    for table in _tables_referencing(conn, "decks"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM decks")
    conn.executemany(
        """INSERT INTO decks (
            fingerprint, commander_name, commander_oracle_id, source, source_url,
            source_deck_id, declared_bracket, source_price_usd, date_added, author, raw_metadata
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (d.fingerprint, d.commander_name, d.commander_oracle_id, d.source, d.source_url,
             d.source_deck_id, d.declared_bracket, d.source_price_usd, d.date_added, d.author,
             json.dumps(d.raw_metadata))
            for d in decks
        ],
    )
    deck_card_rows = [
        (d.fingerprint, oracle_id, qty)
        for d in decks
        for oracle_id, qty in d.card_quantities.items()
    ]
    conn.executemany(
        "INSERT INTO deck_cards (fingerprint, oracle_id, quantity) VALUES (?,?,?)",
        deck_card_rows,
    )


def write_unresolved_report(unresolved: list[UnresolvedCard]) -> None:
    path = REPORTS_DIR / "deck_cards_unresolved.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "source_deck_id", "commander_name", "card_name", "reason"])
        for u in unresolved:
            writer.writerow([u.source, u.source_deck_id, u.commander_name, u.card_name, u.reason])
    print(f"[resolve] wrote {len(unresolved)} unresolved card rows to {path}")


def refresh_all() -> None:
    records = list(read_deck_records(DECK_RECORDS_PATH))
    print(f"[resolve] read {len(records)} raw deck records")
    with connect() as conn:
        resolved, unresolved = resolve_deck_records(conn, records)
        print(f"[resolve] resolved {len(resolved)} decks "
              f"({len(records) - len(resolved)} dropped: nothing resolvable)")
        deduped = dedupe_decks(resolved)
        print(f"[resolve] {len(deduped)} decks after fingerprint dedup "
              f"({len(resolved) - len(deduped)} duplicates collapsed)")
        store_decks(conn, deduped)
    write_unresolved_report(unresolved)

    by_source: dict[str, int] = defaultdict(int)
    for d in deduped:
        by_source[d.source] += 1
    print("[resolve] final per-source breakdown:")
    for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"    {source}: {count}")


if __name__ == "__main__":
    refresh_all()
