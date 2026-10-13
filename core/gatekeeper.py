"""Gatekeeper: internet search + clean data caching in Turso.

Flow: normalize query -> look in knowledge_cache (fresh?) -> otherwise search
DuckDuckGo, read the top pages, clean the text, store it, return it.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from .db import db_session, rows_to_dicts

CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "7"))
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "6"))
PAGES_TO_READ = int(os.getenv("PAGES_TO_READ", "3"))
CHARS_PER_PAGE = 2500
MAX_DOWNLOAD_BYTES = 1_500_000
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class GatekeeperError(Exception):
    pass


def normalize_query(query: str) -> str:
    return " ".join(query.lower().split())[:200]


# ---------- internet ----------
def _ddg_search(query: str, max_results: int) -> List[dict]:
    try:
        from ddgs import DDGS  # new package name
    except ImportError:
        from duckduckgo_search import DDGS  # old package name
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def _read_page(url: str) -> str:
    """Download a page and return its main readable text ('' on any failure)."""
    try:
        if not url.startswith(("http://", "https://")):
            return ""
        with requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=8, stream=True
        ) as r:
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200 or "html" not in ctype:
                return ""
            raw = b""
            for chunk in r.iter_content(65536):
                raw += chunk
                if len(raw) > MAX_DOWNLOAD_BYTES:
                    break
            encoding = r.encoding or "utf-8"
        soup = BeautifulSoup(raw.decode(encoding, errors="ignore"), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                         "form", "noscript", "svg", "iframe"]):
            tag.decompose()
        root = soup.find("article") or soup.find("main") or soup.body or soup
        lines = []
        for line in root.get_text("\n").splitlines():
            line = " ".join(line.split())
            # keep real sentences / code-ish lines, drop menu fragments
            if len(line) >= 30 or any(c in line for c in "(){}=;"):
                lines.append(line)
        return "\n".join(lines)[:CHARS_PER_PAGE]
    except Exception:
        return ""


async def _fetch_from_internet(query: str):
    try:
        results = await asyncio.to_thread(_ddg_search, query, SEARCH_MAX_RESULTS)
    except Exception as e:
        raise GatekeeperError(f"Web search failed (maybe rate-limited): {e}")
    if not results:
        raise GatekeeperError("Web search returned no results.")

    urls = [r.get("href") or r.get("url") or "" for r in results[:PAGES_TO_READ]]
    pages = await asyncio.gather(*[asyncio.to_thread(_read_page, u) for u in urls])

    parts = [f"# Research: {query}"]
    sources = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        url = r.get("href") or r.get("url") or ""
        snippet = (r.get("body") or "").strip()
        sources.append({"title": title, "url": url})
        block = [f"## {i}. {title}", f"URL: {url}", f"Summary: {snippet}"]
        if i <= len(pages) and pages[i - 1]:
            block.append(f"Page content:\n{pages[i - 1]}")
        parts.append("\n".join(block))
    return "\n\n".join(parts), sources


# ---------- cache ----------
def _is_fresh(fetched_at: Optional[str]) -> bool:
    if not fetched_at:
        return False
    try:
        ts = datetime.strptime(str(fetched_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return datetime.now(timezone.utc).replace(tzinfo=None) - ts < timedelta(days=CACHE_TTL_DAYS)


async def search_and_cache(query: str, force: bool = False) -> dict:
    """Return cached data when fresh, otherwise search the web and cache it."""
    key = normalize_query(query)
    if not key:
        raise GatekeeperError("Empty query.")

    cached = None
    async with db_session() as db:
        rs = await db.execute(
            "SELECT * FROM knowledge_cache WHERE query_keyword = ?", [key]
        )
        rows = rows_to_dicts(rs)
        cached = rows[0] if rows else None

    if cached and not force and _is_fresh(cached.get("fetched_at")):
        return {
            "query": key, "cached": True, "clean_data": cached["clean_data"],
            "sources": json.loads(cached.get("sources") or "[]"),
        }

    try:
        clean_data, sources = await _fetch_from_internet(key)
    except GatekeeperError:
        if cached:  # internet failed -> better stale than nothing
            return {
                "query": key, "cached": True, "stale": True,
                "clean_data": cached["clean_data"],
                "sources": json.loads(cached.get("sources") or "[]"),
            }
        raise

    async with db_session() as db:
        await db.execute(
            """INSERT INTO knowledge_cache (query_keyword, clean_data, sources, fetched_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(query_keyword) DO UPDATE SET
                 clean_data = excluded.clean_data,
                 sources = excluded.sources,
                 fetched_at = CURRENT_TIMESTAMP""",
            [key, clean_data, json.dumps(sources)],
        )
    return {"query": key, "cached": False, "clean_data": clean_data,
            "sources": sources}


async def find_cached_hints(words: List[str], limit: int = 3) -> List[dict]:
    """Look for cached knowledge whose keyword contains any of `words`."""
    words = [w.lower() for w in words if len(w) > 2][:8]
    if not words:
        return []
    cond = " OR ".join(["query_keyword LIKE ?"] * len(words))
    async with db_session() as db:
        rs = await db.execute(
            f"SELECT query_keyword, clean_data FROM knowledge_cache "
            f"WHERE {cond} ORDER BY fetched_at DESC LIMIT ?",
            [f"%{w}%" for w in words] + [limit],
        )
        return rows_to_dicts(rs)
