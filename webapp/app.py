"""Flask backend for the local web UI: browse/filter/annotate the ranked
corpus, and trigger any pipeline stage (including a targeted commander
deep-pull) as a background job, all without touching a terminal.

Every route is a thin wrapper over code that already exists and is
independently testable from the CLI (bracket_ranker/*, run_*.py,
pull_commander.py) -- this file adds no new pipeline logic, only HTTP.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from bracket_ranker.db import connect
from bracket_ranker.rank.explain import build_explanation
from webapp import actions
from webapp.jobs import get_job, list_jobs, start_job

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__, static_folder=None)


# --- static frontend -----------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    return send_from_directory(STATIC_DIR, filename)


# --- decks -----------------------------------------------------------

DECK_LIST_COLUMNS = [
    "d.fingerprint", "d.commander_name", "d.source", "d.source_url",
    "s.pct_owned", "s.usd_to_complete", "s.bracket_floor", "s.combo_count",
    "s.top_combo_pieces", "s.top_combo_result", "s.median_assembly_turn",
    "s.game_changer_count", "s.has_mass_land_denial",
    "s.tutor_count", "s.interaction_count", "s.avg_mana_value",
    "sc.declared_bracket_raw", "sc.feel_score", "sc.feel_bracket",
    "sc.confidence", "sc.low_confidence_reason",
    "COALESCE(a.starred, 0) AS starred", "COALESCE(a.rejected, 0) AS rejected",
    "a.notes",
]

SORTABLE = {
    "usd_to_complete": "s.usd_to_complete",
    "pct_owned": "s.pct_owned",
    "feel_score": "sc.feel_score",
    "confidence": "sc.confidence",
    "feel_bracket": "sc.feel_bracket",
}


@app.get("/api/decks")
def api_decks():
    args = request.args
    clauses = []
    params: dict = {}

    if commander := args.get("commander"):
        clauses.append("d.commander_name LIKE :commander")
        params["commander"] = f"%{commander}%"
    if source := args.get("source"):
        sources = [s.strip() for s in source.split(",") if s.strip()]
        placeholders = ",".join(f":source{i}" for i in range(len(sources)))
        clauses.append(f"d.source IN ({placeholders})")
        for i, s in enumerate(sources):
            params[f"source{i}"] = s
    if bracket := args.get("bracket"):
        clauses.append("sc.feel_bracket = :bracket")
        params["bracket"] = int(bracket)
    if max_cost := args.get("max_cost"):
        clauses.append("s.usd_to_complete <= :max_cost")
        params["max_cost"] = float(max_cost)
    if min_pct_owned := args.get("min_pct_owned"):
        clauses.append("s.pct_owned >= :min_pct_owned")
        params["min_pct_owned"] = float(min_pct_owned)
    if min_confidence := args.get("min_confidence"):
        clauses.append("sc.confidence >= :min_confidence")
        params["min_confidence"] = float(min_confidence)
    if args.get("starred_only") == "1":
        clauses.append("COALESCE(a.starred, 0) = 1")
    if args.get("show_rejected") != "1":
        clauses.append("COALESCE(a.rejected, 0) = 0")

    sort_col = SORTABLE.get(args.get("sort_by", "usd_to_complete"), "s.usd_to_complete")
    sort_dir = "DESC" if args.get("sort_dir") == "desc" else "ASC"
    limit = min(int(args.get("limit", 50)), 500)
    offset = int(args.get("offset", 0))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT {", ".join(DECK_LIST_COLUMNS)}
        FROM decks d
        JOIN deck_signals s ON s.fingerprint = d.fingerprint
        LEFT JOIN deck_scores sc ON sc.fingerprint = d.fingerprint
        LEFT JOIN deck_annotations a ON a.fingerprint = d.fingerprint
        {where}
        ORDER BY {sort_col} {sort_dir} NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    count_query = f"""
        SELECT COUNT(*) FROM decks d
        JOIN deck_signals s ON s.fingerprint = d.fingerprint
        LEFT JOIN deck_scores sc ON sc.fingerprint = d.fingerprint
        LEFT JOIN deck_annotations a ON a.fingerprint = d.fingerprint
        {where}
    """
    with connect() as conn:
        total = conn.execute(count_query, params).fetchone()[0]
        rows = conn.execute(query, {**params, "limit": limit, "offset": offset}).fetchall()

    return jsonify({"total": total, "limit": limit, "offset": offset,
                     "decks": [dict(r) for r in rows]})


@app.get("/api/decks/<fingerprint>")
def api_deck_detail(fingerprint: str):
    with connect() as conn:
        data = build_explanation(conn, fingerprint)
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.post("/api/decks/<fingerprint>/annotation")
def api_set_annotation(fingerprint: str):
    body = request.get_json(force=True) or {}
    with connect() as conn:
        deck = conn.execute("SELECT 1 FROM decks WHERE fingerprint = ?", (fingerprint,)).fetchone()
        if not deck:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            """INSERT INTO deck_annotations (fingerprint, starred, rejected, notes, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                   starred = excluded.starred, rejected = excluded.rejected,
                   notes = excluded.notes, updated_at = excluded.updated_at""",
            (
                fingerprint,
                int(bool(body.get("starred", False))),
                int(bool(body.get("rejected", False))),
                body.get("notes"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return jsonify({"ok": True})


@app.get("/api/commanders")
def api_commanders():
    q = request.args.get("q", "")
    with connect() as conn:
        rows = conn.execute(
            """SELECT commander_name, COUNT(*) AS n FROM decks
               WHERE commander_name LIKE ?
               GROUP BY commander_name ORDER BY n DESC LIMIT 30""",
            (f"%{q}%",),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# --- dashboard ---------------------------------------------------------

@app.get("/api/summary")
def api_summary():
    with connect() as conn:
        source_counts = {
            r["source"]: r["n"] for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM decks GROUP BY source"
            )
        }
        latest_run = conn.execute(
            "SELECT * FROM calibration_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        latest_run_dict = dict(latest_run) if latest_run else None
        if latest_run_dict and latest_run_dict.get("feature_importances"):
            latest_run_dict["feature_importances"] = json.loads(latest_run_dict["feature_importances"])

        price_xcheck = conn.execute(
            """SELECT COUNT(*) AS n,
                      AVG(ratio) AS mean_ratio,
                      SUM(CASE WHEN ratio > 1.0 THEN 1 ELSE 0 END) AS n_over_one
               FROM archidekt_price_crosscheck"""
        ).fetchone()
        spellbook_xcheck = conn.execute(
            """SELECT COUNT(*) AS n,
                      SUM(CASE WHEN comparable THEN 1 ELSE 0 END) AS n_comparable,
                      SUM(CASE WHEN agrees THEN 1 ELSE 0 END) AS n_agree
               FROM spellbook_crosscheck"""
        ).fetchone()

    return jsonify({
        "source_counts": source_counts,
        "total_decks": sum(source_counts.values()),
        "latest_calibration_run": latest_run_dict,
        "price_crosscheck": dict(price_xcheck) if price_xcheck and price_xcheck["n"] else None,
        "spellbook_crosscheck": dict(spellbook_xcheck) if spellbook_xcheck and spellbook_xcheck["n"] else None,
    })


# --- jobs ---------------------------------------------------------

@app.get("/api/jobs")
def api_jobs():
    return jsonify([j.to_dict() for j in list_jobs()])


@app.get("/api/jobs/<job_id>")
def api_job_detail(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(job.to_dict())


@app.post("/api/jobs/pull_commander")
def api_pull_commander():
    body = request.get_json(force=True) or {}
    commander = body.get("commander", "").strip()
    if not commander:
        return jsonify({"error": "commander is required"}), 400
    target = int(body.get("target", 300))
    job_id = start_job(
        f"pull_commander: {commander}",
        lambda: actions.action_pull_commander(commander, target),
    )
    return jsonify({"job_id": job_id})


STAGE_ACTIONS = {
    "collection": lambda params: actions.action_run_collection(),
    "ingest": lambda params: actions.action_run_ingest(set(params.get("skip", []))),
    "resolve": lambda params: actions.action_run_resolve(),
    "analyze": lambda params: actions.action_run_analyze(),
    "calibrate": lambda params: actions.action_run_calibrate(),
    "export": lambda params: actions.action_run_export(),
    "full_pipeline": lambda params: actions.action_run_full_pipeline(),
    "price_crosscheck": lambda params: actions.action_run_price_crosscheck(),
    "spellbook_crosscheck": lambda params: actions.action_run_spellbook_crosscheck(
        int(params.get("sample_size", 50))
    ),
}


@app.post("/api/jobs/run_stage")
def api_run_stage():
    body = request.get_json(force=True) or {}
    stage = body.get("stage")
    action = STAGE_ACTIONS.get(stage)
    if action is None:
        return jsonify({"error": f"unknown stage {stage!r}, must be one of {list(STAGE_ACTIONS)}"}), 400
    params = body.get("params", {})
    job_id = start_job(f"stage: {stage}", lambda: action(params))
    return jsonify({"job_id": job_id})


if __name__ == "__main__":
    app.run(debug=False, port=5000)
