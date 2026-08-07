"""MTGJSON precon adapter: our Bracket 2 floor (spec 1.2).

Every preconstructed Commander deck WotC has ever printed, straight from
the format's own steward, and reliably Bracket 2 out of the box -- so
unlike every other source, we don't need a declared_bracket field to know
the label here; the spec states it directly, and we set it accordingly.

Bonus: each card in MTGJSON's dump already carries
identifiers.scryfallOracleId, so -- same as Archidekt -- this source
resolves to our card spine with zero name-matching.
"""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Iterator

import requests

from bracket_ranker.config import (
    MTGJSON_ALLDECKFILES_URL,
    MTGJSON_CACHE_DIR,
    MTGJSON_CACHE_MAX_AGE_HOURS,
    MTGJSON_DECKLIST_URL,
)
from bracket_ranker.http_utils import DEFAULT_HEADERS
from bracket_ranker.ingest.base import CardEntry, DeckRecord

DECKLIST_PATH = MTGJSON_CACHE_DIR / "DeckList.json"
ALLDECKFILES_PATH = MTGJSON_CACHE_DIR / "AllDeckFiles.zip"
PRECON_BRACKET = 2  # per spec 1.2: precons are the Bracket 2 ground truth


def _is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    return (time.time() - path.stat().st_mtime) / 3600 > MTGJSON_CACHE_MAX_AGE_HOURS


def _download(url: str, dest: Path) -> None:
    print(f"[mtgjson] downloading {dest.name} ...")
    with requests.get(url, headers=DEFAULT_HEADERS, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.replace(dest)


def _ensure_cached(force: bool = False) -> None:
    if force or _is_stale(DECKLIST_PATH):
        _download(MTGJSON_DECKLIST_URL, DECKLIST_PATH)
    else:
        print("[mtgjson] DeckList.json cache is fresh")
    if force or _is_stale(ALLDECKFILES_PATH):
        _download(MTGJSON_ALLDECKFILES_URL, ALLDECKFILES_PATH)
    else:
        print("[mtgjson] AllDeckFiles.zip cache is fresh")


def _to_card_entries(card_objs: list[dict]) -> list[CardEntry]:
    return [
        CardEntry(
            name=c["name"],
            quantity=c.get("count", 1),
            oracle_id=c.get("identifiers", {}).get("scryfallOracleId"),
        )
        for c in card_objs
    ]


class MtgjsonPreconAdapter:
    SOURCE_NAME = "mtgjson_precon"

    def __init__(self, force_refresh: bool = False):
        self._force_refresh = force_refresh

    def fetch(self) -> Iterator[DeckRecord]:
        _ensure_cached(force=self._force_refresh)
        deck_list = json.load(open(DECKLIST_PATH, encoding="utf-8"))["data"]
        commander_entries = [d for d in deck_list if d["type"] == "Commander Deck"]

        with zipfile.ZipFile(ALLDECKFILES_PATH) as zf:
            member_names = set(zf.namelist())
            for entry in commander_entries:
                member = f"AllDeckFiles/{entry['fileName']}.json"
                if member not in member_names:
                    print(f"[mtgjson] no deck file for {entry['fileName']!r}, skipping")
                    continue
                deck = json.loads(zf.read(member))["data"]

                commanders = deck.get("commander", [])
                if not commanders:
                    continue
                cards = _to_card_entries(commanders) + _to_card_entries(deck.get("mainBoard", []))

                yield DeckRecord(
                    source="mtgjson_precon",
                    source_url=entry.get("source", "https://mtgjson.com"),
                    source_deck_id=entry["fileName"],
                    commander_name=" + ".join(c["name"] for c in commanders),
                    cards=cards,
                    declared_bracket=PRECON_BRACKET,
                    date_added=entry.get("releaseDate"),
                    raw_metadata={"set_code": entry.get("code"), "deck_name": deck.get("name")},
                )


if __name__ == "__main__":
    n = 0
    for deck in MtgjsonPreconAdapter().fetch():
        n += 1
    print(f"[mtgjson] parsed {n} precon decks")
