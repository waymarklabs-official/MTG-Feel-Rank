"""(de)serialize DeckRecord/CardEntry to/from the Stage 1 JSONL artifact.

This one flat file -- data/cache/ingest_raw/deck_records.jsonl -- IS Stage
1's on-disk contract with Stage 2. Stage 2 never re-runs an adapter; it just
reads this file. Re-running Stage 1 overwrites it from scratch (each
adapter's own per-item cache on disk, e.g. archidekt/{id}.json, is what
makes that cheap to redo).
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Iterator

from bracket_ranker.ingest.base import CardEntry, DeckRecord


def write_deck_records(path: Path, records: Iterator[DeckRecord]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(dataclasses.asdict(record)) + "\n")
            n += 1
    return n


def read_deck_records(path: Path) -> Iterator[DeckRecord]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            raw = json.loads(line)
            raw["cards"] = [CardEntry(**c) for c in raw["cards"]]
            yield DeckRecord(**raw)
