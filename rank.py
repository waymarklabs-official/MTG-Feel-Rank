"""Stage 5.2/5.3 CLI. Two modes:

    python rank.py --commander "Prosper, Tome-Bound" --bracket 4 --max-cost 500
    python rank.py --explain <deck_fingerprint>

The known-limitations banner (spec requirement) prints on every query.
"""
from __future__ import annotations

import argparse

from bracket_ranker.db import connect
from bracket_ranker.rank.explain import explain_deck
from bracket_ranker.rank.limitations import print_limitations
from bracket_ranker.rank.query import QueryFilters, explain_one_line, run_query


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank Commander decks by cost-to-complete and feel score")
    parser.add_argument("--commander", help="substring match on commander name")
    parser.add_argument("--bracket", type=int, choices=range(1, 6), help="target feel_bracket")
    parser.add_argument("--max-cost", type=float, help="maximum usd_to_complete")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--explain", metavar="FINGERPRINT", help="print a full breakdown for one deck")
    args = parser.parse_args()

    with connect() as conn:
        if args.explain:
            explain_deck(conn, args.explain)
            return

        filters = QueryFilters(
            commander=args.commander,
            bracket=args.bracket,
            max_cost=args.max_cost,
            min_confidence=args.min_confidence,
            top_n=args.top_n,
        )
        rows = run_query(conn, filters)

    if not rows:
        print("No decks matched. Try loosening --max-cost, --min-confidence, or --bracket.")
    else:
        print(f"Top {len(rows)} matches, sorted by cost to complete:\n")
        for i, row in enumerate(rows, 1):
            print(f"{i}. {explain_one_line(row)}")
            print(f"    (fingerprint: {row['fingerprint']} -- rerun with "
                  f"--explain {row['fingerprint']} for the full breakdown)\n")

    print_limitations()


if __name__ == "__main__":
    main()
