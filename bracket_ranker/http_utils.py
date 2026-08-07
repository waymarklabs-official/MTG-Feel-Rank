"""Shared "polite GET" helper: every network-bound adapter in this project
hits a public, rate-limited API. Rather than reimplement backoff-on-429 in
each adapter, they all share this one function.
"""
from __future__ import annotations

import time

import requests

from bracket_ranker.config import USER_AGENT

DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def get_json_with_backoff(
    url: str,
    headers: dict | None = None,
    params: dict | None = None,
    max_retries: int = 8,
    initial_delay: float = 5.0,
    timeout: float = 60.0,
) -> dict:
    """GET a JSON endpoint, retrying with escalating delay on 429/5xx.

    A flat "just fetch it" loop works fine until the corpus gets into the
    thousands of decks, at which point a public API's rate limiter WILL
    trip mid-run -- so every adapter that talks to a real API goes through
    here rather than calling requests.get directly.
    """
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    delay = initial_delay
    for attempt in range(max_retries):
        resp = requests.get(url, headers=merged_headers, params=params, timeout=timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = float(resp.headers.get("Retry-After", delay))
            print(f"\n[http] {resp.status_code} from {url}, "
                  f"sleeping {retry_after:.0f}s (attempt {attempt + 1})...")
            time.sleep(retry_after)
            delay = min(delay * 2, 60)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Gave up on {url} after {max_retries} retries")
