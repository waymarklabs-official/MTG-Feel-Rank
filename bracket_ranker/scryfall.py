"""Scryfall bulk data: download, cache, and load into the oracle_id spine.

Two bulk files are used, for two different jobs:
  - "oracle_cards": one row per oracle_id. This is the card-identity table
    (name, text, color identity, game_changer flag). Exactly one printing's
    price is attached, which is NOT what we want for cost math.
  - "default_cards": one row per English printing (plus any card with no
    English printing). This is what lets us (a) map a specific printing's
    Scryfall ID -- which is what ManaBox and Archidekt both give us -- back
    to its oracle_id, and (b) find the *cheapest* printing of a card, which
    is the actually-relevant price for "what would it cost to complete this
    deck".

Both are streamed line-by-line so we never hold the whole file in memory.
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

from bracket_ranker.config import (
    SCRYFALL_BULK_DATA_API,
    SCRYFALL_CACHE_DIR,
    SCRYFALL_CACHE_MAX_AGE_HOURS,
    USER_AGENT,
)
from bracket_ranker.db import connect

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


@dataclass
class BulkFile:
    type: str
    local_path: Path


def _fetch_bulk_index() -> list[dict]:
    resp = requests.get(SCRYFALL_BULK_DATA_API, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]


def _is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours > SCRYFALL_CACHE_MAX_AGE_HOURS


def _download_gz(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers=HEADERS, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(dest)


def ensure_bulk_files(force: bool = False) -> dict[str, Path]:
    """Return local paths for oracle_cards and default_cards, refreshing
    whichever is older than SCRYFALL_CACHE_MAX_AGE_HOURS (or all, if forced).
    """
    wanted = {"oracle_cards", "default_cards"}
    paths: dict[str, Path] = {}
    index = None
    for bulk_type in wanted:
        local_path = SCRYFALL_CACHE_DIR / f"{bulk_type}.jsonl.gz"
        paths[bulk_type] = local_path
        if force or _is_stale(local_path):
            if index is None:
                index = _fetch_bulk_index()
            entry = next(e for e in index if e["type"] == bulk_type)
            print(f"[scryfall] downloading {bulk_type} ...")
            _download_gz(entry["jsonl_download_uri"], local_path)
        else:
            print(f"[scryfall] {bulk_type} cache is fresh, skipping download")
    return paths


def iter_jsonl_gz(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _oracle_text(card: dict) -> str:
    """Concatenate face text for double-faced/split/adventure cards so combo
    and interaction text-matching (Stage 3) sees both halves."""
    if "oracle_text" in card:
        return card["oracle_text"]
    faces = card.get("card_faces") or []
    return "\n//\n".join(face.get("oracle_text", "") for face in faces)


def _is_basic_land(card: dict) -> bool:
    # Scryfall's type_line for basics is "Basic Land -- X" but snow basics
    # are "Basic Snow Land -- X", so check the two supertype words
    # independently rather than the literal substring "Basic Land".
    # This needs no hand-maintained name list and survives new basics.
    type_line = card.get("type_line", "")
    return "Basic" in type_line and "Land" in type_line


def load_cards_table(conn: sqlite3.Connection, oracle_cards_path: Path) -> int:
    rows = []
    for card in iter_jsonl_gz(oracle_cards_path):
        if card.get("layout") == "art_series" or "oracle_id" not in card:
            continue  # art series cards and similar have no oracle_id/text
        type_line = card.get("type_line", "")
        rows.append((
            card["oracle_id"],
            card["name"],
            card.get("layout"),
            card.get("mana_cost", ""),
            card.get("cmc", 0.0),
            type_line,
            _oracle_text(card),
            json.dumps(card.get("color_identity", [])),
            1 if card.get("game_changer") else 0,
            1 if "Land" in type_line else 0,
            1 if _is_basic_land(card) else 0,
            card.get("prices", {}).get("usd"),
            card.get("prices", {}).get("usd_foil"),
            card.get("scryfall_uri"),
            json.dumps(card["produced_mana"]) if card.get("produced_mana") else None,
        ))
    conn.executemany(
        """INSERT INTO cards (
            oracle_id, name, layout, mana_cost, cmc, type_line, oracle_text,
            color_identity, game_changer, is_land, is_basic_land,
            usd_min, usd_min_foil, scryfall_uri, produced_mana
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(oracle_id) DO UPDATE SET
            name=excluded.name, layout=excluded.layout,
            mana_cost=excluded.mana_cost, cmc=excluded.cmc,
            type_line=excluded.type_line, oracle_text=excluded.oracle_text,
            color_identity=excluded.color_identity,
            game_changer=excluded.game_changer, is_land=excluded.is_land,
            is_basic_land=excluded.is_basic_land,
            produced_mana=excluded.produced_mana
        """,
        rows,
    )
    return len(rows)


def load_printings_table(conn: sqlite3.Connection, default_cards_path: Path) -> int:
    # default_cards includes tokens/emblems/etc that have an oracle_id but
    # aren't in oracle_cards (the real-card spine); skip those so printings
    # only ever points at oracle_ids that exist in the cards table.
    known_oracle_ids = {row[0] for row in conn.execute("SELECT oracle_id FROM cards")}
    rows = []
    for card in iter_jsonl_gz(default_cards_path):
        if card.get("oracle_id") not in known_oracle_ids:
            continue
        prices = card.get("prices", {})
        rows.append((
            card["id"],
            card["oracle_id"],
            card.get("set"),
            prices.get("usd"),
            prices.get("usd_foil"),
            card.get("lang"),
        ))
    conn.executemany(
        """INSERT INTO printings (scryfall_id, oracle_id, set_code, usd, usd_foil, lang)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(scryfall_id) DO UPDATE SET
               oracle_id=excluded.oracle_id, set_code=excluded.set_code,
               usd=excluded.usd, usd_foil=excluded.usd_foil, lang=excluded.lang
        """,
        rows,
    )
    # Cheapest known printing (nonfoil first-class, foil-only cards fall
    # back to usd_foil) becomes the card's completion price.
    conn.execute("""
        UPDATE cards SET usd_min = (
            SELECT MIN(usd) FROM printings
            WHERE printings.oracle_id = cards.oracle_id AND usd IS NOT NULL
        )
    """)
    conn.execute("""
        UPDATE cards SET usd_min_foil = (
            SELECT MIN(usd_foil) FROM printings
            WHERE printings.oracle_id = cards.oracle_id AND usd_foil IS NOT NULL
        )
    """)
    return len(rows)


_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Casefold, strip punctuation (curly/straight apostrophes, commas,
    hyphens...), and collapse whitespace, so 'Æther Vial', 'AEther Vial',
    and stray curly quotes all converge on one lookup key."""
    name = name.replace("Æ", "ae").replace("æ", "ae")
    name = name.casefold()
    name = _PUNCT_RE.sub(" ", name)
    return _WS_RE.sub(" ", name).strip()


def build_name_index(conn: sqlite3.Connection) -> dict[str, str]:
    """name-variant -> oracle_id, built fresh from the cards table.

    Indexes: full oracle name, front-face name (for '//' cards), and the
    normalized form of both. Kept in-memory/rebuilt per run rather than
    persisted, since it's cheap to build (~30k rows) and always reflects
    whatever bulk data is currently loaded.
    """
    index: dict[str, str] = {}
    cur = conn.execute("SELECT oracle_id, name FROM cards")
    for oracle_id, name in cur.fetchall():
        variants = {name}
        if " // " in name:
            variants.add(name.split(" // ")[0])
        for v in list(variants):
            variants.add(normalize_name(v))
        for v in variants:
            # First writer wins; true collisions are rare enough that the
            # unresolved report (Stage 2) is where the operator should
            # actually inspect them, not silently overwrite here.
            index.setdefault(v, oracle_id)
    return index


def refresh_all(force: bool = False) -> None:
    paths = ensure_bulk_files(force=force)
    with connect() as conn:
        n_cards = load_cards_table(conn, paths["oracle_cards"])
        n_printings = load_printings_table(conn, paths["default_cards"])
    print(f"[scryfall] loaded {n_cards} oracle cards, {n_printings} printings")


if __name__ == "__main__":
    import sys
    refresh_all(force="--force" in sys.argv)
