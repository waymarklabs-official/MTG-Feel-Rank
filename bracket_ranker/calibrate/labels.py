"""Stage 4.1/4.2: assemble training labels and handle self-declared vs.
rules-floor conflicts.

Per the user's explicit decision: a deck whose declared bracket sits below
its own rules-based floor (e.g. a self-declared Bracket 3 running nine
Game Changers) gets corrected to the floor for TRAINING purposes only --
declared_bracket_used = max(declared_bracket_raw, bracket_floor) -- while
declared_bracket_raw stays intact everywhere for transparency. This is
"drop vs. correct", decided as "correct, keep original visible": correcting
preserves more of an already-small labeled dataset, and sandbagging skews
low (never high), so max() is the right direction of correction.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class LabeledDeck:
    fingerprint: str
    source: str
    declared_bracket_raw: int
    declared_bracket_used: int
    label_conflict: bool
    bracket_floor: int
    features: dict


def load_labeled_decks(conn: sqlite3.Connection) -> list[LabeledDeck]:
    rows = conn.execute(
        """
        SELECT d.fingerprint, d.source, d.declared_bracket,
               s.bracket_floor, s.game_changer_count, s.has_mass_land_denial,
               s.combo_count, s.top_combo_relevance, s.median_assembly_turn,
               s.tutor_count, s.interaction_count, s.avg_mana_value,
               s.ramp_count, s.fast_mana_count
        FROM decks d
        JOIN deck_signals s ON s.fingerprint = d.fingerprint
        WHERE d.declared_bracket IS NOT NULL
        """
    ).fetchall()

    out = []
    for row in rows:
        raw = row["declared_bracket"]
        floor = row["bracket_floor"]
        out.append(LabeledDeck(
            fingerprint=row["fingerprint"],
            source=row["source"],
            declared_bracket_raw=raw,
            declared_bracket_used=max(raw, floor),
            label_conflict=raw < floor,
            bracket_floor=floor,
            features={
                "game_changer_count": row["game_changer_count"],
                "has_mass_land_denial": row["has_mass_land_denial"],
                "bracket_floor": floor,
                "combo_count": row["combo_count"],
                "top_combo_relevance": row["top_combo_relevance"],
                "median_assembly_turn": row["median_assembly_turn"],
                "tutor_count": row["tutor_count"],
                "interaction_count": row["interaction_count"],
                "avg_mana_value": row["avg_mana_value"],
                "ramp_count": row["ramp_count"],
                "fast_mana_count": row["fast_mana_count"],
            },
        ))
    return out


def report_label_conflicts(decks: list[LabeledDeck]) -> None:
    conflicts = [d for d in decks if d.label_conflict]
    if not decks:
        print("[labels] no labeled decks found")
        return
    rate = len(conflicts) / len(decks)
    print(f"[labels] {len(conflicts)}/{len(decks)} decks ({rate:.1%}) declared a bracket "
          f"below their rules-based floor -- corrected to the floor for training")
    if conflicts:
        avg_gap = sum(d.bracket_floor - d.declared_bracket_raw for d in conflicts) / len(conflicts)
        print(f"[labels] average sandbag gap among conflicts: {avg_gap:.2f} brackets")
