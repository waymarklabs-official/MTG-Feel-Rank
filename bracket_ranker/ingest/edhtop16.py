"""EDHTop16 adapter: our Bracket 5 (cEDH) ceiling (spec 1.3).

Verified live against EDHTop16's GraphQL API (introspected at
https://edhtop16.com/api/graphql): most tournament *entries* only carry a
`decklist` field that's a URL to topdeck.gg (the tournament-running
platform), not an actual card list -- only a subset have EDHTop16's own
parsed `maindeck`. We only yield entries with a populated maindeck; this
makes our B5 sample smaller than the full entry count, but every deck we do
get is a real card list (via `Card.oracleId`, so -- like Archidekt and
MTGJSON -- no name-matching needed) from a tournament win/loss record, not
a guess.

Every deck from this source is, by construction, cEDH -- so declared_bracket
is hardcoded to 5, matching the spec's framing of this source as a ground
-truth *label* source, not just a corpus.
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

from bracket_ranker.config import (
    EDHTOP16_GRAPHQL_URL,
    EDHTOP16_PAGE_SIZE,
    EDHTOP16_TOURNAMENT_TARGET,
)
from bracket_ranker.http_utils import DEFAULT_HEADERS
from bracket_ranker.ingest.base import CardEntry, DeckRecord

CEDH_BRACKET = 5  # per spec 1.3: EDHTop16 decks are cEDH by definition

TOURNAMENTS_QUERY = """
query($first: Int!, $after: String) {
  tournaments(first: $first, sortBy: DATE, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        TID
        name
        tournamentDate
        entries {
          standing
          wins
          losses
          draws
          winRate
          priceUsd
          commander { name }
          maindeck { oracleId name }
        }
      }
    }
  }
}
"""


def _post_graphql(query: str, variables: dict, max_retries: int = 5) -> dict:
    delay = 3.0
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                EDHTOP16_GRAPHQL_URL,
                headers={**DEFAULT_HEADERS, "Content-Type": "application/json"},
                json={"query": query, "variables": variables},
                timeout=60,
            )
        except requests.Timeout:
            print(f"\n[edhtop16] timed out, retrying (attempt {attempt + 1})...")
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            print(f"\n[edhtop16] {resp.status_code}, retrying (attempt {attempt + 1})...")
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload:
            raise RuntimeError(f"EDHTop16 GraphQL error: {payload['errors']}")
        return payload["data"]
    raise RuntimeError(f"Gave up on EDHTop16 GraphQL query after {max_retries} retries")


class EdhTop16Adapter:
    SOURCE_NAME = "edhtop16"

    def fetch(self) -> Iterator[DeckRecord]:
        after = None
        tournaments_seen = 0
        decks_yielded = 0

        while tournaments_seen < EDHTOP16_TOURNAMENT_TARGET:
            try:
                data = _post_graphql(
                    TOURNAMENTS_QUERY,
                    {"first": EDHTOP16_PAGE_SIZE, "after": after},
                )
            except RuntimeError as e:
                # A page that won't respond after retries shouldn't cost us
                # every tournament already scanned -- stop cleanly and keep
                # what we have rather than propagating and losing it all.
                print(f"\n[edhtop16] giving up after repeated failures ({e}); "
                      f"stopping with {decks_yielded} decks from {tournaments_seen} tournaments")
                return
            connection = data["tournaments"]
            edges = connection["edges"]
            if not edges:
                break

            for edge in edges:
                node = edge["node"]
                tournaments_seen += 1
                for i, entry in enumerate(node["entries"]):
                    maindeck = entry.get("maindeck") or []
                    commander_name = (entry.get("commander") or {}).get("name")
                    if not maindeck or not commander_name:
                        continue
                    # EDHTop16 joins partner commanders with " / " (verified
                    # live, e.g. "Rograkh, Son of Rohgahh / Tevesh Szat,
                    # Doom of Fools"); Archidekt and MTGJSON both use " + ".
                    # Normalize so resolve.py's commander-name splitting
                    # (which relies on " + ") works the same for every
                    # source instead of silently failing to resolve
                    # commander_oracle_id for every EDHTop16 partner deck.
                    commander_name = commander_name.replace(" / ", " + ")
                    # EDHTop16's `maindeck` is the 99, not the full 100 --
                    # the commander itself is a separate field with no
                    # oracleId of its own (verified live: `cardDetail` is
                    # null on every Commander object, partner or not). We
                    # add it here as a name-only CardEntry so Stage 2's
                    # existing per-card name resolution picks it up like
                    # any other card; skipping this would silently drop
                    # the commander from combo/Game-Changer/interaction
                    # analysis -- a real problem for cEDH decks, where the
                    # commander is very often itself a combo piece.
                    cards = [
                        CardEntry(name=c["name"], quantity=1, oracle_id=c.get("oracleId"))
                        for c in maindeck
                    ] + [
                        CardEntry(name=name, quantity=1)
                        for name in commander_name.split(" + ")
                    ]
                    price = entry.get("priceUsd") or None
                    yield DeckRecord(
                        source="edhtop16",
                        source_url=f"https://edhtop16.com/tournament/{node['TID']}",
                        source_deck_id=f"{node['TID']}-{i}",
                        commander_name=commander_name,
                        cards=cards,
                        declared_bracket=CEDH_BRACKET,
                        source_price_usd=price,
                        date_added=node.get("tournamentDate"),
                        raw_metadata={
                            "tournament_name": node.get("name"),
                            "standing": entry.get("standing"),
                            "wins": entry.get("wins"),
                            "losses": entry.get("losses"),
                            "draws": entry.get("draws"),
                            "win_rate": entry.get("winRate"),
                        },
                    )
                    decks_yielded += 1

            page_info = connection["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            after = page_info["endCursor"]
            print(f"\r[edhtop16] {tournaments_seen} tournaments scanned, "
                  f"{decks_yielded} decks with usable maindecks...", end="", flush=True)
            time.sleep(0.5)
        print()


if __name__ == "__main__":
    n = 0
    for deck in EdhTop16Adapter().fetch():
        n += 1
    print(f"[edhtop16] done: {n} decks parsed")
