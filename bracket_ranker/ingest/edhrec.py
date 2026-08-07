"""EDHREC average-decks adapter (spec 1.5): a small, clean sanity baseline.

Unlike the other "thin scraper" candidates evaluated from mazz3rr/mtg
(TappedOut, Deckstats, MTGTop8), EDHREC's average-decks page has no deck
*discovery* problem: EDHREC computes one average decklist per commander, and
we already have a list of commanders from the other sources. So instead of
crawling user pages or event listings (fragile, and mostly outside this
project's actual goal), we just ask EDHREC for the commander names we
already care about -- including, concretely, Prosper, Tome-Bound.

Implementation is a single regex pulling the page's embedded
`__NEXT_DATA__` JSON (Next.js apps ship their server-rendered props this
way) -- no HTML-parsing library needed for this one page shape.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Iterable, Iterator

import requests

from bracket_ranker.config import EDHREC_CACHE_DIR
from bracket_ranker.http_utils import DEFAULT_HEADERS
from bracket_ranker.ingest.base import CardEntry, DeckRecord

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)
REQUEST_DELAY_SECONDS = 1.0


def _slugify(commander_name: str) -> str:
    """Mirror EDHREC's own URL convention (verified live against
    'Prosper, Tome-Bound' -> 'prosper-tome-bound'): lowercase, drop
    punctuation, collapse whitespace to hyphens."""
    slug = commander_name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug.strip("-")


def _fetch_page(slug: str) -> str | None:
    cache_path = EDHREC_CACHE_DIR / f"{slug}.html"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    url = f"https://edhrec.com/average-decks/{slug}"
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=30)
    time.sleep(REQUEST_DELAY_SECONDS)
    if resp.status_code != 200:
        return None
    cache_path.write_text(resp.text, encoding="utf-8")
    return resp.text


def _parse_page(html: str, commander_name: str, slug: str) -> DeckRecord | None:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        page_data = json.loads(match.group(1))
        deck = page_data["props"]["pageProps"]["data"]["deck"]
        bracket_counts = page_data["props"]["pageProps"]["data"].get("bracket_counts")
    except (KeyError, json.JSONDecodeError):
        return None

    cards = [CardEntry(name=name, quantity=qty)
             for category in deck["cards"].values()
             for name, qty in category]
    if not cards:
        return None

    return DeckRecord(
        source="edhrec_average",
        source_url=f"https://edhrec.com/average-decks/{slug}",
        source_deck_id=slug,
        commander_name=commander_name,
        cards=cards,
        declared_bracket=None,  # this is an aggregate, not one person's judgment
        raw_metadata={"bracket_counts": bracket_counts} if bracket_counts else {},
    )


class EdhrecAverageAdapter:
    """Takes an explicit list of commander names rather than discovering
    its own -- this source answers "what does the average X deck look
    like", which only makes sense for commanders we already know about."""

    SOURCE_NAME = "edhrec_average"

    def __init__(self, commander_names: Iterable[str]):
        # Skip partner-pair commanders ("A + B"): EDHREC's slug convention
        # for partner pages isn't the simple single-name pattern this
        # adapter relies on, and guessing wrong would silently fetch the
        # wrong page instead of just skipping it.
        self._commander_names = sorted({
            name for name in commander_names if " + " not in name
        })

    def fetch(self) -> Iterator[DeckRecord]:
        for name in self._commander_names:
            slug = _slugify(name)
            html = _fetch_page(slug)
            if html is None:
                continue
            record = _parse_page(html, name, slug)
            if record is not None:
                yield record


if __name__ == "__main__":
    for deck in EdhrecAverageAdapter(["Prosper, Tome-Bound"]).fetch():
        print(deck.commander_name, len(deck.cards), "cards, bracket_counts=",
              deck.raw_metadata.get("bracket_counts"))
