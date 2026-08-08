"""Archidekt adapter: highest-priority source per the spec.

Archidekt is simultaneously our corpus, our label source (edhBracket is a
real user-set field -- verified live), and a price cross-check, because its
card objects already carry `oracleCard.uid`, which -- verified live against
Scryfall -- IS the Scryfall oracle_id. That means Archidekt decks resolve
to our card spine for free, no name-matching involved.

Archidekt's public list endpoint (`/api/decks/v3/`) has no trustworthy
total count (its "count" field reads a flat 1000 for every query we tried,
regardless of actual result size -- a capped estimate, not a real count) so
we paginate by incrementing `page` ourselves and stop when a page comes
back empty, bounded by the per-run budgets in config.py.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Iterator

import requests

from bracket_ranker.config import (
    ARCHIDEKT_BASE_URL,
    ARCHIDEKT_CACHE_DIR,
    ARCHIDEKT_REQUEST_DELAY_SECONDS,
    ARCHIDEKT_TARGET_GENERAL,
    ARCHIDEKT_TARGET_PER_BRACKET,
)
from bracket_ranker.http_utils import get_json_with_backoff
from bracket_ranker.ingest.base import CardEntry, DeckRecord

COMMANDER_FORMAT_ID = 3  # verified live: formats=3 consistently returns 100-card decks


def _iter_deck_ids(extra_params: dict, target: int, seen: set[int]) -> Iterator[int]:
    collected = 0
    page = 1
    while collected < target:
        params = {"formats": COMMANDER_FORMAT_ID, "page": page, **extra_params}
        payload = get_json_with_backoff(f"{ARCHIDEKT_BASE_URL}/decks/v3/", params=params)
        results = payload.get("results", [])
        if not results:
            break
        for row in results:
            deck_id = row["id"]
            if deck_id in seen:
                continue
            seen.add(deck_id)
            yield deck_id
            collected += 1
            if collected >= target:
                break
        page += 1
        time.sleep(ARCHIDEKT_REQUEST_DELAY_SECONDS)


def _get_deck_detail(deck_id: int) -> dict | None:
    cache_path = ARCHIDEKT_CACHE_DIR / f"{deck_id}.json"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    try:
        detail = get_json_with_backoff(f"{ARCHIDEKT_BASE_URL}/decks/{deck_id}/")
    except requests.HTTPError as e:
        print(f"[archidekt] skipping deck {deck_id}: {e}")
        return None
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(detail, f)
    time.sleep(ARCHIDEKT_REQUEST_DELAY_SECONDS)
    return detail


def _parse_deck(detail: dict, enforce_size_sanity: bool = True) -> DeckRecord | None:
    commander_names = []
    cards: list[CardEntry] = []
    tcg_total = 0.0
    has_price_data = False

    for entry in detail.get("cards", []):
        categories = entry.get("categories") or []
        if "Maybeboard" in categories:
            continue  # not actually in the deck -- a sideboard/wishlist category
        oracle_card = entry.get("card", {}).get("oracleCard")
        if not oracle_card or not oracle_card.get("uid"):
            continue  # customCards (homebrew proxies) have no Scryfall identity
        quantity = entry.get("quantity", 1)
        cards.append(CardEntry(
            name=oracle_card["name"],
            quantity=quantity,
            oracle_id=oracle_card["uid"],
        ))
        if "Commander" in categories:
            commander_names.append(oracle_card["name"])
        # Verified live (found via the Archidekt price cross-check turning
        # up a $164 Archidekt total on a deck with three $200-300 Reserved
        # List dual lands in it): `prices.tcg` is frequently 0.0 even when
        # the card has a real, populated `prices.tcgMinimum` -- `tcg` looks
        # like "this one specific listing", which apparently doesn't sync
        # for every card, while `tcgMinimum` is the actual floor price and
        # is reliably populated. Using `tcg` silently zeroed out exactly
        # the highest-value cards in a deck.
        prices = entry.get("card", {}).get("prices", {})
        tcg_price = prices.get("tcgMinimum") or prices.get("tcg")
        if tcg_price:
            tcg_total += tcg_price * quantity
            has_price_data = True

    if not cards or not commander_names:
        return None  # not a resolvable/complete Commander decklist

    # Archidekt's deck builder doesn't enforce the 100-card singleton rule
    # at save time, so `deckFormat=3` also catches brainstorm dumps and
    # "list of every card I own that fits this commander"-style pages.
    # Verified live: a 312-card deck literally named "big deck" turned up
    # in the raw pull. A real (possibly partner/companion) Commander deck
    # lands close to 100; anything well outside that isn't one.
    # Skipped for an explicit single-deck import (enforce_size_sanity=False):
    # if someone deliberately imports one exact deck by URL, honor whatever
    # it actually is (a work-in-progress, a Duel Commander list, a Companion
    # deck at 101) rather than second-guessing their choice the way the
    # broad corpus sweep needs to, to filter out actual junk.
    total_quantity = sum(c.quantity for c in cards)
    if enforce_size_sanity and not (90 <= total_quantity <= 110):
        return None

    return DeckRecord(
        source="archidekt",
        source_url=f"https://archidekt.com/decks/{detail['id']}",
        source_deck_id=str(detail["id"]),
        commander_name=" + ".join(commander_names),
        cards=cards,
        declared_bracket=detail.get("edhBracket"),
        source_price_usd=round(tcg_total, 2) if has_price_data else None,
        date_added=detail.get("createdAt"),
        author=(detail.get("owner") or {}).get("username"),
        raw_metadata={
            "viewCount": detail.get("viewCount"),
            "deckFormat": detail.get("deckFormat"),
        },
    )


_DECK_ID_RE = re.compile(r"(?:archidekt\.com/decks/)?(\d+)")


def parse_deck_id(url_or_id: str) -> int | None:
    """Accepts a full Archidekt deck URL, a bare deck ID, or anything with
    the ID embedded (e.g. pasted with a trailing slug/query string)."""
    match = _DECK_ID_RE.search(url_or_id.strip())
    return int(match.group(1)) if match else None


def fetch_one_deck(url_or_id: str) -> DeckRecord | None:
    """Import one specific deck by URL/ID -- for testing your own decks,
    which may not be anywhere in the sampled corpus at all. Bypasses the
    100-card sanity filter the broad sweep needs (see _parse_deck): you
    asked for this exact deck, so you get this exact deck back, whatever
    its actual card count is.
    """
    deck_id = parse_deck_id(url_or_id)
    if deck_id is None:
        print(f"[archidekt] couldn't find a deck ID in {url_or_id!r}")
        return None
    detail = _get_deck_detail(deck_id)
    if detail is None:
        return None
    record = _parse_deck(detail, enforce_size_sanity=False)
    if record is None:
        print(f"[archidekt] deck {deck_id} has no commander or no resolvable cards")
    return record


def fetch_for_commander(commander_name: str, target: int = 300) -> Iterator[DeckRecord]:
    """Targeted deep-pull for one commander, bypassing the broad per-bracket
    sweep entirely.

    The broad sweep in ArchidektAdapter.fetch() samples across ALL
    commanders, so any single commander gets only whatever decks happen to
    land in that sample -- for a corpus of a few thousand decks across
    ~1,100 distinct commanders, that's often a handful at best. Verified
    live: Archidekt's public list endpoint accepts `commanderName=<exact
    name>` (undocumented -- found by inspecting the site's own search UI
    network traffic, not guessed) and returns real, correctly filtered
    results, unlike the `commanders=`/`commander=` param names that seemed
    plausible but are silently ignored. This is the tool for "I want to
    seriously evaluate commander X", not "grow the general corpus".
    """
    seen_ids: set[int] = set()
    deck_ids = list(_iter_deck_ids(
        {"commanderName": commander_name, "orderBy": "-viewCount"}, target, seen_ids
    ))
    print(f"[archidekt] commanderName={commander_name!r}: {len(deck_ids)} candidate deck ids")
    for deck_id in deck_ids:
        detail = _get_deck_detail(deck_id)
        if detail is None:
            continue
        record = _parse_deck(detail)
        if record is not None:
            yield record


class ArchidektAdapter:
    SOURCE_NAME = "archidekt"

    def fetch(self) -> Iterator[DeckRecord]:
        seen_ids: set[int] = set()
        sweeps: list[tuple[str, dict, int]] = [
            (f"bracket {b}", {"edhBracket": b}, ARCHIDEKT_TARGET_PER_BRACKET)
            for b in range(1, 6)
        ]
        sweeps.append(("general breadth", {"orderBy": "-viewCount"}, ARCHIDEKT_TARGET_GENERAL))

        for label, params, target in sweeps:
            deck_ids = list(_iter_deck_ids(params, target, seen_ids))
            print(f"[archidekt] {label}: {len(deck_ids)} candidate deck ids")
            for deck_id in deck_ids:
                detail = _get_deck_detail(deck_id)
                if detail is None:
                    continue
                record = _parse_deck(detail)
                if record is not None:
                    yield record


if __name__ == "__main__":
    n = 0
    for deck in ArchidektAdapter().fetch():
        n += 1
        if n % 100 == 0:
            print(f"[archidekt] parsed {n} decks so far...")
    print(f"[archidekt] done: {n} decks parsed")
