"""The specific long-running operations the web UI can trigger, each
wrapped as a zero-argument callable for jobs.start_job(). Every one of
these is just calling code that already exists and is independently
testable from the CLI -- the web layer adds no new pipeline logic, only a
way to trigger and watch it.
"""
from __future__ import annotations

import dataclasses
import json

import run_analyze
import run_calibrate
import run_export
import run_ingest
import pull_commander
from bracket_ranker.analyze.price_crosscheck import (
    report_price_crosscheck,
    run_price_crosscheck,
    store_price_crosscheck,
)
from bracket_ranker.calibrate.spellbook_crosscheck import (
    report_crosscheck,
    run_crosscheck,
    store_crosscheck,
)
from bracket_ranker.collection import refresh_all as collection_refresh_all
from bracket_ranker.config import DECK_RECORDS_PATH
from bracket_ranker.db import connect
from bracket_ranker.resolve import refresh_all as resolve_refresh_all


def action_pull_commander(commander: str, target: int) -> dict:
    n = pull_commander.run(commander, target=target)
    return {"commander": commander, "decks_added": n}


def action_import_deck(url_or_id: str) -> dict:
    """Import one specific deck by Archidekt URL/ID -- for testing your
    own decks, which may not be anywhere in the sampled corpus. Only runs
    Stage 2 (resolve) afterward, not the full pipeline: that's enough to
    make the deck queryable and stress-testable immediately, without
    forcing a slow corpus-wide recalibration just to import one deck. Run
    "Rebuild everything" from the Pipeline tab afterward if you also want
    it scored/ranked in Explorer.
    """
    from bracket_ranker.ingest.archidekt import fetch_one_deck

    record = fetch_one_deck(url_or_id)
    if record is None:
        raise ValueError(
            f"couldn't import a deck from {url_or_id!r} -- check the URL/ID "
            f"and that the deck has a commander set"
        )

    with open(DECK_RECORDS_PATH, "a", encoding="utf-8") as out:
        out.write(json.dumps(dataclasses.asdict(record)) + "\n")

    resolve_refresh_all()

    with connect() as conn:
        row = conn.execute(
            "SELECT fingerprint FROM decks WHERE source='archidekt' AND source_deck_id=?",
            (record.source_deck_id,),
        ).fetchone()

    return {
        "commander_name": record.commander_name,
        "fingerprint": row[0] if row else None,
        "source_url": record.source_url,
    }


def action_run_collection() -> dict:
    """Re-parse ManaBox_Collection.csv -- run this after buying/adding
    cards and re-exporting from ManaBox, then Analyze to recompute cost/
    ownership against the refreshed collection."""
    result = collection_refresh_all()
    return {
        "owned_cards": len(result.owned),
        "unresolved": len(result.unresolved),
        "resolved_by_id": result.resolved_by_id,
        "resolved_by_name": result.resolved_by_name,
        "resolved_by_api": result.resolved_by_api,
    }


def action_run_ingest(skip: set[str]) -> dict:
    return run_ingest.run(skip=skip)


def action_run_resolve() -> None:
    resolve_refresh_all()


def action_run_analyze() -> None:
    run_analyze.refresh_all()


def action_run_calibrate() -> None:
    run_calibrate.main()


def action_run_export() -> None:
    run_export.refresh_all()


def action_run_full_pipeline() -> None:
    """Stages 2-5 in order -- the "just make everything consistent again"
    button, for after a manual DB edit or a partial run."""
    resolve_refresh_all()
    run_analyze.refresh_all()
    run_calibrate.main()
    run_export.refresh_all()


def action_run_price_crosscheck() -> dict:
    with connect() as conn:
        rows = run_price_crosscheck(conn)
        store_price_crosscheck(conn, rows)
    report_price_crosscheck(rows)
    return {"n_compared": len(rows)}


def action_run_spellbook_crosscheck(sample_size: int) -> dict:
    with connect() as conn:
        results = run_crosscheck(conn, sample_size=sample_size)
        store_crosscheck(conn, results)
    report_crosscheck(results)
    comparable = [r for r in results if r["comparable"]]
    agree_rate = (
        sum(1 for r in comparable if r["agrees"]) / len(comparable) if comparable else None
    )
    return {"n_sampled": len(results), "n_comparable": len(comparable), "agree_rate": agree_rate}
