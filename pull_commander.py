"""Targeted deep-pull for one commander, then re-run Stages 2-5 so the
rank CLI immediately reflects the new decks.

The broad Stage 1 sweep (run_ingest.py) samples across ALL commanders, so
any one commander -- Prosper, Tome-Bound included -- only gets whatever
lands in that sample. This is the tool for "I'm seriously evaluating
commander X and want a real shortlist", not for growing the general
corpus. It APPENDS to the existing Stage 1 artifact rather than
overwriting it, so running this for several commanders over time keeps
building up the corpus rather than resetting it.

Usage: python pull_commander.py "Prosper, Tome-Bound" [--target 300]
"""
from __future__ import annotations

import argparse
import dataclasses
import json

import run_analyze
import run_calibrate
import run_export
from bracket_ranker.config import DECK_RECORDS_PATH
from bracket_ranker.ingest.archidekt import fetch_for_commander
from bracket_ranker.resolve import refresh_all as resolve_refresh_all


def run(commander: str, target: int = 300) -> int:
    """Programmatic entry point (the web app's job runner calls this
    directly, in-process -- argparse only lives in main() below)."""
    n = 0
    with open(DECK_RECORDS_PATH, "a", encoding="utf-8") as out:
        for record in fetch_for_commander(commander, target=target):
            out.write(json.dumps(dataclasses.asdict(record)) + "\n")
            n += 1
    print(f"[pull_commander] appended {n} decks for {commander!r} to {DECK_RECORDS_PATH}")

    if n == 0:
        print("[pull_commander] nothing new found -- check the spelling matches Scryfall's "
              "oracle name exactly (e.g. commas and hyphens matter)")
        return 0

    print("\n[pull_commander] re-running Stages 2-5 so `rank` reflects the new decks...\n")
    resolve_refresh_all()
    run_analyze.refresh_all()
    run_calibrate.main()
    run_export.refresh_all()
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep-pull Archidekt decks for one commander")
    parser.add_argument("commander", help='e.g. "Prosper, Tome-Bound"')
    parser.add_argument("--target", type=int, default=300,
                         help="max candidate deck ids to pull (default 300)")
    args = parser.parse_args()
    run(args.commander, target=args.target)


if __name__ == "__main__":
    main()
