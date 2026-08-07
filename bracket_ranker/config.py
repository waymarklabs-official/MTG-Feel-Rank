"""Paths, constants, and shared settings for the whole pipeline.

Centralized here so every stage agrees on where cached artifacts live and
how long a cache is considered fresh, without importing each other's modules.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_DIR = DATA_DIR / "db"
REPORTS_DIR = DATA_DIR / "reports"

SCRYFALL_CACHE_DIR = CACHE_DIR / "scryfall"
ARCHIDEKT_CACHE_DIR = CACHE_DIR / "archidekt"
MTGJSON_CACHE_DIR = CACHE_DIR / "mtgjson"
EDHTOP16_CACHE_DIR = CACHE_DIR / "edhtop16"
SPELLBOOK_CACHE_DIR = CACHE_DIR / "spellbook"
EDHREC_CACHE_DIR = CACHE_DIR / "edhrec"

for _d in (
    DATA_DIR, CACHE_DIR, DB_DIR, REPORTS_DIR,
    SCRYFALL_CACHE_DIR, ARCHIDEKT_CACHE_DIR, MTGJSON_CACHE_DIR,
    EDHTOP16_CACHE_DIR, SPELLBOOK_CACHE_DIR, EDHREC_CACHE_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "bracket_ranker.sqlite"
MODEL_PATH = DB_DIR / "ordinal_bracket_model.pkl"

MANABOX_CSV_PATH = PROJECT_ROOT / "ManaBox_Collection.csv"

# Scryfall asks every API consumer to identify itself accurately. This is not
# optional courtesy -- undescriptive User-Agents get throttled or blocked.
# See https://scryfall.com/docs/api
CONTACT_EMAIL = "waymarklabs@gmail.com"
USER_AGENT = f"CommanderBracketRanker/0.1 (+{CONTACT_EMAIL})"

# Scryfall: "Bulk data is updated once daily... if you are casually
# consuming this data... we ask that you retrieve new files no more than
# once per day." We treat our local copy as stale after this many hours.
SCRYFALL_CACHE_MAX_AGE_HOURS = 24

# Spellbook's variants dump changes when new combos are published/errata'd,
# which happens far less often than daily. The spec calls for weekly refresh.
SPELLBOOK_CACHE_MAX_AGE_HOURS = 24 * 7

# New precon products ship a handful of times a year, so a weekly refresh
# is already generous -- no reason to re-pull a 260MB zip on every run.
MTGJSON_CACHE_MAX_AGE_HOURS = 24 * 7

SCRYFALL_BULK_DATA_API = "https://api.scryfall.com/bulk-data"
SPELLBOOK_BASE_URL = "https://backend.commanderspellbook.com"
ARCHIDEKT_BASE_URL = "https://archidekt.com/api"
MTGJSON_ALLDECKFILES_URL = "https://mtgjson.com/api/v5/AllDeckFiles.zip"
MTGJSON_DECKLIST_URL = "https://mtgjson.com/api/v5/DeckList.json"
EDHTOP16_GRAPHQL_URL = "https://edhtop16.com/api/graphql"

# Bracket rules, verified 2026-08-05 against WotC's Commander Brackets Beta
# announcements (Introducing Commander Brackets Beta; Oct 21 2025 update;
# Feb 9 2026 update) rather than assumed from training data:
#   - Bracket 1 (Exhibition): anything goes, casual "jank" builds
#   - Bracket 2 (Core):       precon-level power, no Game Changers
#   - Bracket 3 (Upgraded):   up to 3 Game Changers, no mass land denial,
#                             two-card combos OK only if not assembled early
#   - Bracket 4 (Optimized):  Game Changers/MLD/combos all unrestricted
#   - Bracket 5 (cEDH):       tournament-optimized, banned list is the only limit
# WotC frames each bracket by an expected minimum game length (turns before
# a win/loss is plausible): B1 >=9, B2 >=8, B3 >=6, B4 >=4, B5 any turn.
# We reuse that same number (6) as the "early vs. late" combo-assembly
# cutoff for Bracket 3, since it's WotC's own stated floor for that bracket
# and the spec explicitly calls this the easiest rule to get wrong.
# This cutoff is a documented assumption, not an official rule -- it is
# exposed as EARLY_COMBO_TURN_CUTOFF so it can be tuned/challenged later.
GAME_CHANGERS_BRACKET_3_MAX = 3
EARLY_COMBO_TURN_CUTOFF = 6

# Archidekt pull targets. Its public API has no reliable total-count
# (the "count" field is capped/fake -- verified live, it reads 1000 no
# matter the actual filter or true corpus size) so pagination is driven by
# "keep requesting pages until one comes back empty", bounded by these
# per-run budgets. Bumping these and re-running Stage 1 is the intended way
# to grow the corpus later; nothing downstream needs to change.
ARCHIDEKT_TARGET_PER_BRACKET = 400   # decks pulled per edhBracket value (1-5)
ARCHIDEKT_TARGET_GENERAL = 1000      # additional undeclared-bracket decks, for corpus breadth
ARCHIDEKT_REQUEST_DELAY_SECONDS = 0.3

# EDHTop16 target: verified live that most tournament *entries* only carry
# a `decklist` URL (almost all pointing at topdeck.gg, a third site we'd
# have to scrape separately) rather than a parsed `maindeck`. Only entries
# with a populated maindeck are usable, so this is a tournament-page budget,
# not a deck-count budget -- actual yield will be well under this number.
EDHTOP16_TOURNAMENT_TARGET = 800
EDHTOP16_PAGE_SIZE = 20  # smaller pages: the full maindeck query is heavy enough to time out at 50

# Bounds how many distinct commanders (out of however many the corpus turns
# up) get an EDHREC average-deck lookup. Ranked by frequency in the corpus
# so the commanders we actually have the most data on get a baseline first.
EDHREC_MAX_COMMANDERS = 800

INGEST_RAW_DIR = CACHE_DIR / "ingest_raw"
INGEST_RAW_DIR.mkdir(parents=True, exist_ok=True)
DECK_RECORDS_PATH = INGEST_RAW_DIR / "deck_records.jsonl"

# Stage 3.4: simulations per deck combo check. Pure-Python Monte Carlo, so
# this trades run time for precision -- tune down for a faster dev loop,
# up for a more stable median/p25 on the final run.
MONTE_CARLO_SIMULATIONS = 1000

BRACKET_NAMES = {
    1: "Exhibition",
    2: "Core",
    3: "Upgraded",
    4: "Optimized",
    5: "cEDH",
}
