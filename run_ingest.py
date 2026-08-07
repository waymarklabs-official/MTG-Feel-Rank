"""Stage 1 entry point: run every source adapter, write one combined
Stage 1 artifact (data/cache/ingest_raw/deck_records.jsonl) for Stage 2 to
read. Independently re-runnable: each adapter caches its own raw responses
on disk, so re-running this after a partial/failed run is cheap.

Usage: python run_ingest.py [--skip archidekt,edhtop16,...]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from collections import Counter

from bracket_ranker.config import DECK_RECORDS_PATH, EDHREC_MAX_COMMANDERS
from bracket_ranker.ingest.archidekt import ArchidektAdapter
from bracket_ranker.ingest.base import DeckRecord
from bracket_ranker.ingest.edhrec import EdhrecAverageAdapter
from bracket_ranker.ingest.edhtop16 import EdhTop16Adapter
from bracket_ranker.ingest.mtgjson_precons import MtgjsonPreconAdapter

# Order matters only in that EDHREC (last) needs commander names gathered
# from the others first -- it has no discovery mechanism of its own.
PRIMARY_ADAPTERS = {
    "archidekt": ArchidektAdapter,
    "mtgjson_precon": MtgjsonPreconAdapter,
    "edhtop16": EdhTop16Adapter,
}


def run(skip: set[str] = frozenset()) -> dict[str, int]:
    """Programmatic entry point (the web app's job runner calls this
    directly, in-process -- argparse only lives in main() below)."""
    commander_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()

    with open(DECK_RECORDS_PATH, "w", encoding="utf-8") as out:
        def write(record: DeckRecord) -> None:
            out.write(json.dumps(dataclasses.asdict(record)) + "\n")
            commander_counts[record.commander_name] += 1
            source_counts[record.source] += 1

        for name, adapter_cls in PRIMARY_ADAPTERS.items():
            if name in skip:
                print(f"[ingest] skipping {name}")
                continue
            print(f"[ingest] === {name} ===")
            try:
                for record in adapter_cls().fetch():
                    write(record)
            except Exception as e:
                # One source failing (a flaky API, a timeout) shouldn't
                # cost every other source's already-written progress --
                # print it and move on. Re-running later with plain
                # `python run_ingest.py` naturally retries the failed
                # source too (its own per-item cache makes that cheap).
                print(f"\n[ingest] {name} FAILED partway through: {e!r}")
            print(f"[ingest] {name}: {source_counts[name]} decks so far")

        if "edhrec_average" not in skip:
            print("[ingest] === edhrec_average ===")
            top_commanders = [name for name, _ in commander_counts.most_common(EDHREC_MAX_COMMANDERS)]
            try:
                for record in EdhrecAverageAdapter(top_commanders).fetch():
                    write(record)
            except Exception as e:
                print(f"\n[ingest] edhrec_average FAILED partway through: {e!r}")
            print(f"[ingest] edhrec_average: {source_counts['edhrec_average']} decks")

    print(f"[ingest] wrote {sum(source_counts.values())} deck records to {DECK_RECORDS_PATH}")
    print("[ingest] per-source breakdown:")
    for source, count in source_counts.most_common():
        print(f"    {source}: {count}")
    print(f"[ingest] {len(commander_counts)} distinct commanders seen")
    return dict(source_counts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1: pull the deck corpus")
    parser.add_argument(
        "--skip", default="",
        help="comma-separated adapter names to skip, e.g. --skip archidekt,edhtop16",
    )
    args = parser.parse_args()
    run(skip={s.strip() for s in args.skip.split(",") if s.strip()})


if __name__ == "__main__":
    main()
