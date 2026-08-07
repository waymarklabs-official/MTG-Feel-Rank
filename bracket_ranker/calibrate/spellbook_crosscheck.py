"""Spec 0.4: cross-check our own scoring against Spellbook's own
purpose-built /estimate-bracket endpoint, on a sample of decks.

Spellbook's estimator returns one of seven letter tags (bracketTag), not a
1-5 number -- verified live (a real MTGJSON precon comes back "C" /Core,
a real EDHTop16 cEDH deck comes back "R"/Ruthless). Three of the seven
(Spicy, Oddball, Banned) describe deck *flavor* or *legality*, not power
level, and have no honest mapping onto a power scale -- so this reports
"not comparable" for those rather than forcing a bad mapping. The other
four give a genuine, if coarse, independent read on power level.

This is explicitly a cross-check, not a training signal: spec 0.4 calls
for comparing verdicts on a sample, not calling this per-deck across the
whole corpus (that would be exactly the "tens of thousands of network
calls" /find-my-combos problem, just against a different endpoint).
"""
from __future__ import annotations

import random
import sqlite3
import time

from bracket_ranker.spellbook import estimate_bracket

# Each Spellbook tag maps to the *set* of our 1-5 brackets it's compatible
# with (a coarse letter grade spans more than one of our finer brackets).
# None means "this tag isn't a power-level judgment at all".
BRACKET_TAG_COMPATIBLE = {
    "E": {1, 2},   # Exhibition
    "C": {2, 3},   # Core
    "P": {3, 4},   # Powerful
    "R": {4, 5},   # Ruthless
    "S": None,     # Spicy -- unconventional build, not a power tier
    "O": None,     # Oddball -- niche, not a power tier
    "B": None,     # Banned -- a legality flag, not a power tier
}


def _sample_decks(conn: sqlite3.Connection, n: int, seed: int = 42) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT d.fingerprint, d.commander_name, sc.feel_bracket, s.bracket_floor
           FROM decks d
           JOIN deck_scores sc ON sc.fingerprint = d.fingerprint
           JOIN deck_signals s ON s.fingerprint = d.fingerprint"""
    ).fetchall()
    rng = random.Random(seed)
    return rng.sample(rows, min(n, len(rows)))


def _deck_card_names(conn: sqlite3.Connection, fingerprint: str, commander_names: list[str]) -> list[str]:
    rows = conn.execute(
        """SELECT c.name FROM deck_cards dc JOIN cards c ON c.oracle_id = dc.oracle_id
           WHERE dc.fingerprint = ? AND c.is_basic_land = 0""",
        (fingerprint,),
    ).fetchall()
    return [r[0] for r in rows if r[0] not in commander_names]


def run_crosscheck(conn: sqlite3.Connection, sample_size: int = 50) -> list[dict]:
    decks = _sample_decks(conn, sample_size)
    results = []
    for i, deck in enumerate(decks, 1):
        commander_names = deck["commander_name"].split(" + ")
        main = _deck_card_names(conn, deck["fingerprint"], commander_names)
        try:
            resp = estimate_bracket(main, commander_names)
        except Exception as e:
            print(f"\n[spellbook_crosscheck] skipping {deck['fingerprint'][:12]}: {e!r}")
            continue
        tag = resp.get("bracketTag")
        compatible = BRACKET_TAG_COMPATIBLE.get(tag)
        agrees = (compatible is not None and deck["feel_bracket"] in compatible) if compatible else None
        results.append({
            "fingerprint": deck["fingerprint"],
            "commander_name": deck["commander_name"],
            "our_feel_bracket": deck["feel_bracket"],
            "our_bracket_floor": deck["bracket_floor"],
            "spellbook_tag": tag,
            "comparable": compatible is not None,
            "agrees": agrees,
        })
        print(f"\r[spellbook_crosscheck] {i}/{len(decks)}...", end="", flush=True)
        time.sleep(0.3)
    print()
    return results


def report_crosscheck(results: list[dict]) -> None:
    comparable = [r for r in results if r["comparable"]]
    not_comparable = [r for r in results if not r["comparable"]]
    print(f"[spellbook_crosscheck] {len(results)} decks sampled, "
          f"{len(comparable)} comparable (S/O/B tags excluded: {len(not_comparable)})")
    if comparable:
        agree_rate = sum(1 for r in comparable if r["agrees"]) / len(comparable)
        print(f"[spellbook_crosscheck] agreement rate: {agree_rate:.1%}")
        for r in comparable:
            if not r["agrees"]:
                print(f"    DISAGREE: {r['commander_name']!r} -- we say "
                      f"{r['our_feel_bracket']}, Spellbook says {r['spellbook_tag']} "
                      f"(compatible with {sorted(BRACKET_TAG_COMPATIBLE[r['spellbook_tag']])})")


def store_crosscheck(conn: sqlite3.Connection, results: list[dict]) -> None:
    conn.execute("DELETE FROM spellbook_crosscheck")
    conn.executemany(
        """INSERT INTO spellbook_crosscheck (
            fingerprint, our_feel_bracket, our_bracket_floor, spellbook_tag, comparable, agrees
        ) VALUES (:fingerprint, :our_feel_bracket, :our_bracket_floor, :spellbook_tag,
                  :comparable, :agrees)""",
        results,
    )


if __name__ == "__main__":
    from bracket_ranker.db import connect
    with connect() as conn:
        results = run_crosscheck(conn, sample_size=50)
        store_crosscheck(conn, results)
    report_crosscheck(results)
