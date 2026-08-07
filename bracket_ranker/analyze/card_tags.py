"""Card-level "feel" classification (Stage 3.5 + inputs to 3.2/3.3).

Every predicate here is crude oracle-text pattern matching, not real rules
comprehension -- the spec explicitly sanctions this ("crude is fine; it
cheaply recovers a real chunk of feel") in exchange for needing no
hand-maintained card list. Each function documents exactly what it's
looking for and its known false-positive/negative shape, so a grader (or a
student) can see what to tighten first.

Computed once per oracle_id into the `card_tags` table, not per (deck, card)
-- Stage 3 proper just joins against this.
"""
from __future__ import annotations

import re
import sqlite3

# --- Tutors -----------------------------------------------------------
# "Search your library for a card" family. We deliberately split out land
# fetch ("search your library for a Forest card") as ramp/fixing, not a
# tutor in the combo-assembly sense -- that's the actual ambiguous call
# flagged in the spec ("whether a card counts as 'interaction'... when
# classification is ambiguous" applies here too). A land-fetch effect
# rarely finds a specific combo piece, so it stays out of is_tutor.
_LAND_FETCH_RE = re.compile(
    r"search your library for (a|an|up to \w+)[^.]*"
    r"\b(land|plains|island|swamp|mountain|forest|wastes)\b"
)
_TUTOR_RE = re.compile(r"search (your|your own) library for")

# --- Interaction: removal, counterspells, protection -------------------
_INTERACTION_PATTERNS = [
    r"destroy target",
    r"exile target",
    r"counter target spell",
    r"deals? \d+ damage to target creature",
    r"deals? \d+ damage to any target",
    r"return target creature[^.]*to (its|their) owner's hand",
    r"-\d+/-\d+",
    r"\bfight\b",
    r"sacrifices? a creature",
    r"gains? hexproof",
    r"gains? indestructible",
    r"protection from",
    r"can't be countered",
    r"prevent all (combat )?damage",
]
_INTERACTION_RE = re.compile("|".join(_INTERACTION_PATTERNS))

# --- Ramp: mana rocks and land-fetch --------------------------------
# Deliberately NOT requiring a mana-symbol curly brace after "add": some
# rocks spell it out in words ("Add one mana of any color", e.g. Arcane
# Signet) rather than with a symbol.
_MANA_ROCK_RE = re.compile(r"\{t\}[^.]*add\b")

# --- Fast mana: cheap, immediate, disproportionate acceleration --------
# Matches the spec's own examples (Sol Ring, Moxen, rituals, Ancient Tomb):
# a permanent/spell that can put 2+ mana into play in one shot (Sol Ring,
# Ancient Tomb, Dark Ritual)...
_BIG_MANA_RE = re.compile(r"add (\{[wubrgc0-9/]+\}\s*){2,}")
# ...OR any 0-cost mana rock (Moxen: only 1 mana produced, but at zero
# investment that's still exactly the kind of "fast" the spec means).
_ANY_MANA_RE = _MANA_ROCK_RE


def classify_card(oracle_text: str, type_line: str, cmc: float) -> dict[str, bool]:
    text = (oracle_text or "").lower()
    type_line = (type_line or "").lower()

    is_land_fetch = bool(_LAND_FETCH_RE.search(text))
    is_tutor = bool(_TUTOR_RE.search(text)) and not is_land_fetch

    is_ramp = (
        is_land_fetch
        or ("artifact" in type_line and "equipment" not in type_line
            and "vehicle" not in type_line and bool(_MANA_ROCK_RE.search(text)))
    )

    is_fast_mana = (
        (cmc <= 0 and "artifact" in type_line and bool(_ANY_MANA_RE.search(text)))
        or (bool(_BIG_MANA_RE.search(text)) and (
            cmc <= 1
            or "land" in type_line  # Ancient Tomb et al: no mana cost at all
            or "instant" in type_line or "sorcery" in type_line  # rituals
        ))
    )

    return {
        "is_tutor": is_tutor,
        "is_interaction": bool(_INTERACTION_RE.search(text)),
        "is_ramp": is_ramp,
        "is_fast_mana": is_fast_mana,
    }


# --- Mass land denial ---------------------------------------------------
# Per the spec's quoted WotC definition: destroys/exiles/bounces/locks
# four-or-more lands per player without replacing them. We can't count "how
# many lands" from text reliably, so this matches the well-known SHAPE of
# such effects (symmetric, sweeping land effects) rather than a literal
# number -- known to catch Armageddon/Ravages of War/Catastrophe/Ruination/
# Winter Orb/Static Orb-style effects, known to miss anything phrased
# unusually. Tunable: extend this list rather than rewriting the function.
_MLD_PATTERNS = [
    r"destroy all lands",
    r"destroy all nonbasic lands",
    r"each player sacrifices[^.]*lands",
    r"players? sacrifice[^.]*lands",
    r"lands? (don't|doesn't|do not) untap",
    r"return all lands",
    r"exile all lands",
]
_MLD_RE = re.compile("|".join(_MLD_PATTERNS))


def is_mass_land_denial(oracle_text: str) -> bool:
    return bool(_MLD_RE.search((oracle_text or "").lower()))


def refresh_all(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT oracle_id, oracle_text, type_line, cmc FROM cards").fetchall()
    out = []
    for row in rows:
        tags = classify_card(row["oracle_text"], row["type_line"], row["cmc"] or 0.0)
        out.append((
            row["oracle_id"],
            int(tags["is_tutor"]),
            int(tags["is_interaction"]),
            int(tags["is_ramp"]),
            int(tags["is_fast_mana"]),
            int(is_mass_land_denial(row["oracle_text"])),
        ))
    conn.execute("DELETE FROM card_tags")
    conn.executemany(
        """INSERT INTO card_tags (
            oracle_id, is_tutor, is_interaction, is_ramp, is_fast_mana, is_mass_land_denial
        ) VALUES (?,?,?,?,?,?)""",
        out,
    )
    return len(out)


if __name__ == "__main__":
    from bracket_ranker.db import connect
    with connect() as conn:
        n = refresh_all(conn)
    print(f"[card_tags] classified {n} cards")
