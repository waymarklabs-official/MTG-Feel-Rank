"""SQLite schema and a thin connection helper.

One database, one file, opened fresh by every stage. Tables are additive:
each stage populates or updates its own tables and leaves earlier ones alone,
so stages can be re-run independently against whatever is already on disk.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from bracket_ranker.config import DB_PATH

SCHEMA = """
-- One row per Scryfall oracle_id: the card-identity spine everything else
-- keys against. Never key on name -- see resolve.py for why.
CREATE TABLE IF NOT EXISTS cards (
    oracle_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    layout          TEXT,
    mana_cost       TEXT,
    cmc             REAL,
    type_line       TEXT,
    oracle_text     TEXT,
    color_identity  TEXT,   -- JSON list, e.g. ["U","R"]
    game_changer    INTEGER NOT NULL DEFAULT 0,
    is_land         INTEGER NOT NULL DEFAULT 0,
    is_basic_land   INTEGER NOT NULL DEFAULT 0,
    usd_min         REAL,   -- cheapest known printing price, nonfoil preferred
    usd_min_foil    REAL,
    scryfall_uri    TEXT
);

-- Maps every printing (what ManaBox's "Scryfall ID" column actually is)
-- to its oracle_id, so the collection CSV resolves without ever touching
-- card names.
-- No FK to cards(oracle_id) here: Scryfall's "default_cards" bulk file
-- includes tokens/emblems/etc. that have an oracle_id but never appear in
-- "oracle_cards" (the real-card spine). load_printings_table() filters
-- those out anyway; the relaxed constraint just avoids the load failing
-- if Scryfall ever adds a printing type we haven't seen before.
CREATE TABLE IF NOT EXISTS printings (
    scryfall_id TEXT PRIMARY KEY,
    oracle_id   TEXT NOT NULL,
    set_code    TEXT,
    usd         REAL,
    usd_foil    REAL,
    lang        TEXT
);
CREATE INDEX IF NOT EXISTS idx_printings_oracle ON printings(oracle_id);

-- The user's collection, already reduced to oracle_id + owned quantity
-- (summed across printings/foils/languages). Basic lands are excluded here
-- per the spec, since they're never a completion cost.
CREATE TABLE IF NOT EXISTS collection (
    oracle_id TEXT PRIMARY KEY,
    quantity  INTEGER NOT NULL,
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id)
);

-- One row per deduped deck. fingerprint = sha256 of the sorted oracle_id
-- set, so the same 100-card list posted to two sites collapses to one row.
CREATE TABLE IF NOT EXISTS decks (
    fingerprint         TEXT PRIMARY KEY,
    commander_name      TEXT,
    commander_oracle_id TEXT,
    source              TEXT NOT NULL,
    source_url          TEXT,
    source_deck_id      TEXT,
    declared_bracket    INTEGER,
    source_price_usd    REAL,
    date_added          TEXT,
    author              TEXT,
    raw_metadata        TEXT    -- JSON blob of whatever else the source gave us
);
CREATE INDEX IF NOT EXISTS idx_decks_commander ON decks(commander_oracle_id);
CREATE INDEX IF NOT EXISTS idx_decks_source ON decks(source);

-- Normalized deck contents, one row per (deck, card). Lets us do real SQL
-- joins for ownership/cost/combo-intersection instead of parsing JSON blobs.
CREATE TABLE IF NOT EXISTS deck_cards (
    fingerprint TEXT NOT NULL,
    oracle_id   TEXT NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (fingerprint, oracle_id),
    FOREIGN KEY (fingerprint) REFERENCES decks(fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_deck_cards_oracle ON deck_cards(oracle_id);

-- Commander Spellbook's combo variants, pulled in bulk and cached locally.
-- variant_id is Spellbook's own id (e.g. "513-5034--46").
CREATE TABLE IF NOT EXISTS combos (
    variant_id    TEXT PRIMARY KEY,
    oracle_ids    TEXT NOT NULL,  -- JSON list of the combo's card oracle_ids
    piece_count   INTEGER NOT NULL,
    produces      TEXT,           -- JSON list of Spellbook "produces" feature tags
    is_game_ender INTEGER NOT NULL DEFAULT 0,
    is_infinite   INTEGER NOT NULL DEFAULT 0,  -- any produced feature is Spellbook-flagged "uncountable"
    raw           TEXT            -- full Spellbook variant JSON, for debugging
);

-- Card-level classification tags (Stage 3), computed once per oracle_id
-- via crude oracle-text pattern matching (see analyze/card_tags.py) and
-- reused everywhere a deck needs to know "is this a tutor/ramp/etc" --
-- rather than re-running regexes over the same card text once per deck.
CREATE TABLE IF NOT EXISTS card_tags (
    oracle_id            TEXT PRIMARY KEY,
    is_tutor              INTEGER NOT NULL DEFAULT 0,
    is_interaction         INTEGER NOT NULL DEFAULT 0,
    is_ramp                INTEGER NOT NULL DEFAULT 0,
    is_fast_mana           INTEGER NOT NULL DEFAULT 0,
    is_mass_land_denial    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id)
);

-- Per-deck analysis output from Stage 3. Recomputed wholesale each run.
CREATE TABLE IF NOT EXISTS deck_signals (
    fingerprint          TEXT PRIMARY KEY,
    pct_owned            REAL,
    usd_to_complete      REAL,
    missing_no_price     INTEGER,
    game_changer_count   INTEGER,
    has_mass_land_denial INTEGER,
    bracket_floor        INTEGER,
    combo_count          INTEGER,
    top_combo_variant_id TEXT,
    top_combo_pieces     INTEGER,
    top_combo_result     TEXT,
    top_combo_relevance  REAL,
    median_assembly_turn REAL,
    p25_assembly_turn    REAL,
    mana_model_version   TEXT,
    tutor_count          INTEGER,
    interaction_count    INTEGER,
    avg_mana_value       REAL,
    ramp_count           INTEGER,
    fast_mana_count      INTEGER,
    FOREIGN KEY (fingerprint) REFERENCES decks(fingerprint)
);

-- Per-deck calibrated output from Stage 4/5.
CREATE TABLE IF NOT EXISTS deck_scores (
    fingerprint            TEXT PRIMARY KEY,
    declared_bracket_raw   INTEGER,
    declared_bracket_used  INTEGER,  -- after floor-correction, see calibrate/labels.py
    label_conflict         INTEGER NOT NULL DEFAULT 0,
    feel_score             REAL,
    feel_bracket           INTEGER,
    confidence             REAL,
    low_confidence_reason  TEXT,
    FOREIGN KEY (fingerprint) REFERENCES decks(fingerprint)
);

-- Spec 0.4 cross-check: a sample compared against Spellbook's own
-- /estimate-bracket verdict. Recomputed wholesale on demand (network
-- calls, so not run on every Stage 4 pass by default).
CREATE TABLE IF NOT EXISTS spellbook_crosscheck (
    fingerprint           TEXT NOT NULL,
    our_feel_bracket      INTEGER,
    our_bracket_floor     INTEGER,
    spellbook_tag         TEXT,
    comparable            INTEGER NOT NULL,
    agrees                INTEGER,  -- NULL when not comparable
    FOREIGN KEY (fingerprint) REFERENCES decks(fingerprint)
);

-- Spec 3.1 cross-check: our missing-card cost vs. Archidekt's own
-- whole-deck price field, Archidekt decks only. Pure local computation.
CREATE TABLE IF NOT EXISTS archidekt_price_crosscheck (
    fingerprint           TEXT NOT NULL,
    our_usd_to_complete   REAL,
    archidekt_price_usd   REAL,
    ratio                 REAL,
    FOREIGN KEY (fingerprint) REFERENCES decks(fingerprint)
);

-- User review state from the "eyeball test" -- the web UI's way of
-- letting a human's judgment persist alongside the model's. Never
-- overwritten by any Stage 1-5 rerun; only the UI writes here.
CREATE TABLE IF NOT EXISTS deck_annotations (
    fingerprint  TEXT PRIMARY KEY,
    starred      INTEGER NOT NULL DEFAULT 0,
    rejected     INTEGER NOT NULL DEFAULT 0,
    notes        TEXT,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (fingerprint) REFERENCES decks(fingerprint)
);

-- One row per Stage 4 run, so the dashboard can show "how good is the
-- model right now" without re-running calibration on page load.
CREATE TABLE IF NOT EXISTS calibration_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at                TEXT NOT NULL,
    n_labeled             INTEGER,
    label_conflict_rate   REAL,
    train_exact_match     REAL,
    train_within_one      REAL,
    test_exact_match      REAL,
    test_within_one       REAL,
    precon_gate_pass      INTEGER,
    precon_gate_rate      REAL,
    cedh_gate_pass        INTEGER,
    cedh_gate_rate        REAL,
    feature_importances   TEXT  -- JSON: [{threshold, [[name, coef], ...]}, ...]
);
"""


def get_connection() -> sqlite3.Connection:
    """Every connection ensures the schema exists first -- CREATE TABLE IF
    NOT EXISTS is cheap and idempotent, so this costs nothing once the
    tables already exist, but it means a fresh clone with no database file
    yet just works the first time any stage runs. This used to be a
    separate init_db() step nothing called automatically; a user hit
    "no such table: cards" on a completely fresh checkout because of it.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Kept as an explicit, nameable step for CLI use -- get_connection()
    (used everywhere else) already does this on every call, so running
    this separately is never actually required anymore."""
    get_connection().close()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {DB_PATH}")
