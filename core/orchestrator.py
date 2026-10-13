"""Orchestrator: local Ollama (7B) -> research -> Master Plan + Hints -> Turso.

Runs on the LAPTOP (via worker.py). Koyeb cannot reach localhost:11434.
"""
import asyncio
import json
import os
import re
from typing import List, Optional

import requests

from .db import db_session, rows_to_dicts
from .gatekeeper import GatekeeperError, find_cached_hints, search_and_cache

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))  # RTX 3050 = small VRAM
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "900"))
CONTEXT_CHAR_BUDGET = 5000  # keeps the prompt inside a 4k-token window


class OrchestratorError(Exception):
    pass


# ---------- Ollama ----------
def check_ollama() -> None:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
    except Exception as e:
        raise OrchestratorError(
            f"Cannot reach Ollama at {OLLAMA_URL}. Is `ollama serve` running? ({e})"
        )
    names = [m.get("name", "") for m in r.json().get("models", [])]
    if not any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL + ":") for n in names):
        raise OrchestratorError(
            f"Model '{OLLAMA_MODEL}' not installed. Run: ollama pull {OLLAMA_MODEL}"
        )


def _generate_sync(prompt: str, system: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_ctx": OLLAMA_NUM_CTX},
    }
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=OLLAMA_TIMEOUT
        )
        r.raise_for_status()
    except Exception as e:
        raise OrchestratorError(f"Ollama request failed: {e}")
    return r.json().get("response", "")


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError("Model did not return valid JSON")


async def ask_json(prompt: str, system: str, retries: int = 1) -> dict:
    last = None
    for _ in range(retries + 1):
        text = await asyncio.to_thread(_generate_sync, prompt, system)
        try:
            return _extract_json(text)
        except ValueError as e:
            last = e
    raise OrchestratorError(str(last))


# ---------- planning ----------
SYSTEM = (
    "You are a senior software architect. You answer ONLY with valid JSON, "
    "no markdown, no commentary."
)


async def _make_search_queries(goal: str) -> List[str]:
    prompt = (
        f"Project goal:\n{goal}\n\n"
        "Give up to 3 short web search queries (max 8 words each) that would find "
        "the most useful technical documentation for building this.\n"
        'Reply as JSON: {"queries": ["...", "..."]}'
    )
    try:
        data = await ask_json(prompt, SYSTEM)
        qs = [str(q).strip() for q in data.get("queries", []) if str(q).strip()]
        return qs[:3] or [goal[:80]]
    except OrchestratorError:
        return [goal[:80]]


async def _gather_context(goal: str, project_id: int, log) -> str:
    chunks = []

    async with db_session() as db:
        rs = await db.execute(
            "SELECT module_name, hint_content FROM hints_memory "
            "WHERE project_id = ? ORDER BY updated_at DESC LIMIT 5",
            [project_id],
        )
        for h in rows_to_dicts(rs):
            chunks.append(f"[Saved hint: {h['module_name']}] {h['hint_content']}")

    for q in await _make_search_queries(goal):
        try:
            log(f"researching: {q}")
            res = await search_and_cache(q)
            chunks.append(res["clean_data"][:1800])
        except GatekeeperError as e:
            log(f"search skipped ({e})")

    if not chunks:  # no internet -> use anything already cached
        for row in await find_cached_hints(goal.split()):
            chunks.append(row["clean_data"][:1800])

    return "\n\n".join(chunks)[:CONTEXT_CHAR_BUDGET]


def _clean_tasks(data: dict) -> List[dict]:
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise OrchestratorError("Model returned no tasks.")
    out = []
    for t in tasks[:20]:
        if not isinstance(t, dict) or not str(t.get("title", "")).strip():
            continue
        subs = []
        for s in (t.get("subtasks") or [])[:10]:
            if isinstance(s, dict) and str(s.get("title", "")).strip():
                subs.append({"title": str(s["title"]).strip()[:200],
                             "description": str(s.get("description", "")).strip()})
        out.append({
            "title": str(t["title"]).strip()[:200],
            "description": str(t.get("description", "")).strip(),
            "module": str(t.get("module", "")).strip()[:100],
            "hint": str(t.get("hint", "")).strip(),
            "subtasks": subs,
        })
    if not out:
        raise OrchestratorError("No valid tasks in model output.")
    return out


async def generate_master_plan(project_id: int, log=print) -> dict:
    """Research + plan for a project, store tasks and hints in Turso."""
    await asyncio.to_thread(check_ollama)

    async with db_session() as db:
        rs = await db.execute("SELECT * FROM projects WHERE id = ?", [project_id])
        rows = rows_to_dicts(rs)
    if not rows:
        raise OrchestratorError(f"Project {project_id} not found.")
    project = rows[0]
    goal = f"{project['name']}: {project.get('description') or ''}".strip()

    context = await _gather_context(goal, project_id, log)
    log("asking the local model for the master plan...")

    prompt = (
        f"PROJECT GOAL:\n{goal}\n\n"
        f"TECHNICAL NOTES FROM THE INTERNET (may be partial):\n{context or '(none)'}\n\n"
        "Break the project into an ordered master plan of 5-12 concrete engineering "
        "tasks. Each task may have up to 5 subtasks. For each task also give the "
        "'module' (file/component name) and a short practical 'hint' "
        "(libraries, pitfalls, API names - use the notes above when relevant).\n"
        'Reply as JSON exactly like: {"tasks": [{"title": "", "description": "", '
        '"module": "", "hint": "", "subtasks": [{"title": "", "description": ""}]}]}'
    )
    tasks = _clean_tasks(await ask_json(prompt, SYSTEM))

    async with db_session() as db:
        # regenerate: drop old unfinished tasks, keep Done / In Progress ones
        await db.execute(
            "DELETE FROM master_plan WHERE project_id = ? AND status = 'Pending'",
            [project_id],
        )
        n_tasks = n_hints = 0
        for t in tasks:
            res = await db.execute(
                "INSERT INTO master_plan (project_id, parent_id, task_title, "
                "task_description) VALUES (?, NULL, ?, ?)",
                [project_id, t["title"], t["description"]],
            )
            parent_id = res.last_insert_rowid
            n_tasks += 1
            for s in t["subtasks"]:
                await db.execute(
                    "INSERT INTO master_plan (project_id, parent_id, task_title, "
                    "task_description) VALUES (?, ?, ?, ?)",
                    [project_id, parent_id, s["title"], s["description"]],
                )
                n_tasks += 1
            if t["hint"]:
                await db.execute(
                    "INSERT INTO hints_memory (project_id, module_name, hint_content) "
                    "VALUES (?, ?, ?)",
                    [project_id, t["module"] or t["title"], t["hint"]],
                )
                n_hints += 1
    log(f"saved {n_tasks} tasks and {n_hints} hints")
    return {"project_id": project_id, "tasks": n_tasks, "hints": n_hints}
