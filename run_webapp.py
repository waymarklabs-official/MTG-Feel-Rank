"""Launch the local web UI: browse, filter, and annotate the ranked
corpus, and trigger any pipeline stage as a background job -- no need to
come back to a terminal (or a chat session) to run another simulation.

Usage: python run_webapp.py [--port 5000]
"""
from __future__ import annotations

import argparse

from webapp.app import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Bracket Ranker web UI")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"[webapp] serving on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
