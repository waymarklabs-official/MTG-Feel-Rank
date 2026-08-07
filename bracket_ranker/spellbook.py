"""Commander Spellbook client: bulk combo pull + local matching.

Spellbook's own guidance (and basic arithmetic) rules out calling
/find-my-combos per deck: with 30,000+ variants and a corpus in the
thousands of decks, that's tens of thousands of network calls to answer a
question that's really just "which of these known card-sets appear in this
decklist" -- a local set-intersection once we have the variants dump.

So: pull /variants/ once, cache to disk, load into the `combos` table keyed
by oracle_id (never by card name -- Spellbook's own IDs are per-printing-ish
integers that don't matter to us; oracleId is what lets this join against
our deck_cards table). /estimate-bracket is kept separate as a per-deck
cross-check tool for Stage 4, used on a sample, not the whole corpus.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from bracket_ranker.config import (
    SPELLBOOK_BASE_URL,
    SPELLBOOK_CACHE_DIR,
    SPELLBOOK_CACHE_MAX_AGE_HOURS,
    USER_AGENT,
)
from bracket_ranker.db import connect

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
VARIANTS_CACHE_PATH = SPELLBOOK_CACHE_DIR / "variants.jsonl"
# A separate completion marker, stamped only once pagination truly reaches
# its end. The jsonl file's own mtime is NOT a safe freshness signal here:
# resuming an interrupted download touches the file "now", which would
# otherwise make a half-finished download look fresh and stop it from ever
# continuing.
DONE_MARKER_PATH = SPELLBOOK_CACHE_DIR / "variants.done"
PAGE_LIMIT = 1000

# Spellbook tags every combo's "produces" list with plain-English feature
# names (verified live, e.g. Thassa's Oracle + Demonic Consultation ->
# "Win the game"). We treat a combo as game-ending if any produced feature
# matches one of these substrings. This is a heuristic keyword list, not an
# official Spellbook field -- documented here so it's easy to extend if a
# real game-ending combo slips through with different wording.
GAME_ENDING_KEYWORDS = (
    "win the game",
    "opponent loses the game",
    "opponents lose the game",
    "each opponent loses the game",
    "infinite damage",
    "infinite mill",
    "infinite combat damage",
    "lose the game",
)


def _is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours > SPELLBOOK_CACHE_MAX_AGE_HOURS


def _get_with_backoff(url: str) -> dict:
    """GET with retry/backoff on 429s. The bulk pull makes ~30-40 requests
    at PAGE_LIMIT=1000; Spellbook's rate limiter is tight enough that a
    naive back-to-back loop trips it partway through, so every request
    waits its turn and 429s get an escalating sleep instead of failing.
    """
    delay = 5.0
    for attempt in range(8):
        resp = requests.get(url, headers=HEADERS, timeout=60)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", delay))
            print(f"\n[spellbook] rate-limited, sleeping {retry_after:.0f}s "
                  f"(attempt {attempt + 1})...")
            time.sleep(retry_after)
            delay = min(delay * 2, 60)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Gave up on {url} after repeated 429s")


def download_variants(force: bool = False) -> Path:
    if not force and not _is_stale(DONE_MARKER_PATH):
        print("[spellbook] variants cache is complete and fresh, skipping download")
        return VARIANTS_CACHE_PATH

    if force:
        VARIANTS_CACHE_PATH.unlink(missing_ok=True)
        DONE_MARKER_PATH.unlink(missing_ok=True)

    # Resumable: if a previous run got rate-limited (or was interrupted)
    # partway through, pick up from the offset implied by how many variants
    # are already on disk rather than re-downloading from page 1.
    start_offset = 0
    if VARIANTS_CACHE_PATH.exists():
        with open(VARIANTS_CACHE_PATH, encoding="utf-8") as f:
            start_offset = sum(1 for _ in f)
        print(f"[spellbook] resuming from offset {start_offset}")

    url = f"{SPELLBOOK_BASE_URL}/variants/?limit={PAGE_LIMIT}&offset={start_offset}"
    total = start_offset
    with open(VARIANTS_CACHE_PATH, "a", encoding="utf-8") as out:
        while url:
            payload = _get_with_backoff(url)
            for variant in payload["results"]:
                out.write(json.dumps(variant) + "\n")
            out.flush()
            total += len(payload["results"])
            print(f"\r[spellbook] downloaded {total} variants...", end="", flush=True)
            url = payload.get("next")
            time.sleep(1.0)  # stay well under the rate limit for the whole run
    print()
    DONE_MARKER_PATH.touch()
    return VARIANTS_CACHE_PATH


def _is_game_ender(produces: list[dict]) -> bool:
    names = " | ".join(p.get("feature", {}).get("name", "").lower() for p in produces)
    return any(kw in names for kw in GAME_ENDING_KEYWORDS)


def _is_infinite(produces: list[dict]) -> bool:
    # Spellbook tags each produced feature "uncountable" when it represents
    # an unbounded effect ("Infinite colorless mana", "Infinite storm
    # count", etc, verified live) rather than a fixed value -- this is
    # exactly the "two-card *infinite* combo" the bracket rules single out,
    # as opposed to a merely-strong two-card interaction.
    return any(p.get("feature", {}).get("uncountable") for p in produces)


def load_combos_table(path: Path) -> int:
    rows = []
    seen_variant_ids: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            variant = json.loads(line)
            # The download loop appends per-page; a resumed/retried run can
            # leave a handful of duplicate lines behind (observed live: a
            # brief overlap between two runs against the same offset
            # range). Keep the first occurrence, since variant_id is our
            # primary key and every duplicate is byte-identical anyway.
            if variant["id"] in seen_variant_ids:
                continue
            seen_variant_ids.add(variant["id"])
            oracle_ids = sorted({
                use["card"]["oracleId"] for use in variant.get("uses", [])
                if use.get("card", {}).get("oracleId")
            })
            if len(oracle_ids) < 2:
                continue  # not useful for "does the deck contain this combo"
            produces = variant.get("produces", [])
            rows.append((
                variant["id"],
                json.dumps(oracle_ids),
                len(oracle_ids),
                json.dumps([p.get("feature", {}).get("name") for p in produces]),
                1 if _is_game_ender(produces) else 0,
                1 if _is_infinite(produces) else 0,
                json.dumps(variant),
            ))
    with connect() as conn:
        conn.execute("DELETE FROM combos")
        conn.executemany(
            """INSERT INTO combos (
                variant_id, oracle_ids, piece_count, produces, is_game_ender, is_infinite, raw
            ) VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def refresh_all(force: bool = False) -> None:
    path = download_variants(force=force)
    n = load_combos_table(path)
    print(f"[spellbook] loaded {n} combo variants (2+ pieces)")


def estimate_bracket(main_card_names: list[str], commander_names: list[str]) -> dict:
    """Cross-check helper for Stage 4: ask Spellbook's own purpose-built
    bracket estimator about a decklist (by name -- that's what this endpoint
    takes), for comparison against our independently-computed bracket_floor.
    Only meant to be called on a small sample, not the whole corpus.
    """
    body = {
        "main": [{"card": name, "quantity": 1} for name in main_card_names],
        "commanders": [{"card": name, "quantity": 1} for name in commander_names],
    }
    resp = requests.post(
        f"{SPELLBOOK_BASE_URL}/estimate-bracket",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    import sys
    refresh_all(force="--force" in sys.argv)
