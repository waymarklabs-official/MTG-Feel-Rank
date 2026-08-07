"""Shared interface every deck-source adapter implements.

Adding a new source means writing one new module with one class that
implements SourceAdapter -- nothing in Stage 2 onward needs to change, or
even know, how many sources exist. Each adapter yields CardEntry-bearing
DeckRecords; some sources already give us an oracle_id per card (Archidekt),
others only give us a name (MTGJSON, EDHTop16, thin site scrapers) -- both
are legal, and oracle_id resolution for the name-only ones happens once,
uniformly, in resolve.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class CardEntry:
    name: str
    quantity: int = 1
    oracle_id: str | None = None  # filled here only if the source hands it to us for free


@dataclass
class DeckRecord:
    source: str                     # short adapter tag, e.g. "archidekt", "mtgjson_precon"
    source_url: str
    source_deck_id: str
    commander_name: str
    cards: list[CardEntry] = field(default_factory=list)
    declared_bracket: int | None = None
    source_price_usd: float | None = None
    date_added: str | None = None
    author: str | None = None
    raw_metadata: dict = field(default_factory=dict)


class SourceAdapter(Protocol):
    """Every adapter module exposes a SOURCE_NAME and a fetch() generator."""

    SOURCE_NAME: str

    def fetch(self) -> Iterator[DeckRecord]:
        ...
