import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from core.db import db_session, init_database, rows_to_dicts
from core.gatekeeper import GatekeeperError, search_and_cache

VALID_STATUS = {"Pending", "In Progress", "Done"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    yield


# ---------- optional login (set ADMIN_PASSWORD on Koyeb) ----------
security = HTTPBasic(auto_error=False)


def require_auth(creds: Optional[HTTPBasicCredentials] = Depends(security)):
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        return  # auth disabled
    user = os.getenv("ADMIN_USER", "admin")
    ok = (
        creds is not None
        and secrets.compare_digest(creds.username.encode(), user.encode())
        and secrets.compare_digest(creds.password.encode(), password.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Login required",
            headers={"WWW-Authenticate": 'Basic realm="Agent Portal"'},
        )


app = FastAPI(
    title="Local Coding Agent Storage Portal",
    lifespan=lifespan,
    dependencies=[Depends(require_auth)],
)
templates = Jinja2Templates(directory="templates")


# ---------- models ----------
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    force: bool = False


class StatusIn(BaseModel):
    status: str


# ---------- helpers ----------
def order_plan(plans: list, names: dict) -> list:
    """Parents first, each followed by its subtasks."""
    by_parent = {}
    for p in plans:
        by_parent.setdefault(p["parent_id"], []).append(p)
    ordered = []
    for p in sorted(by_parent.get(None, []), key=lambda x: (x["project_id"] or 0, x["id"])):
        p["depth"] = 0
        ordered.append(p)
        for c in sorted(by_parent.get(p["id"], []), key=lambda x: x["id"]):
            c["depth"] = 1
            ordered.append(c)
    for p in ordered:
        p["project_name"] = names.get(p["project_id"], "?")
    return ordered


async def _queue_job(job_type: str, payload: dict) -> int:
    async with db_session() as db:
        res = await db.execute(
            "INSERT INTO jobs (job_type, payload) VALUES (?, ?)",
            [job_type, json.dumps(payload)],
        )
        return res.last_insert_rowid


# ---------- pages ----------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    async with db_session() as db:
        projects = rows_to_dicts(await db.execute("SELECT * FROM projects ORDER BY id DESC"))
        plans = rows_to_dicts(await db.execute("SELECT * FROM master_plan"))
        hints = rows_to_dicts(await db.execute("SELECT * FROM hints_memory ORDER BY id DESC"))
        cache = rows_to_dicts(await db.execute(
            "SELECT id, query_keyword, clean_data, sources, fetched_at "
            "FROM knowledge_cache ORDER BY id DESC LIMIT 100"))
        jobs = rows_to_dicts(await db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 20"))

    names = {p["id"]: p["name"] for p in projects}
    for h in hints:
        h["project_name"] = names.get(h["project_id"], "?")
    for c in cache:
        c["sources"] = json.loads(c["sources"] or "[]")

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "projects": projects,
            "plans": order_plan(plans, names),
            "hints": hints,
            "cache": cache,
            "jobs": jobs,
            "has_active_jobs": any(j["status"] in ("pending", "running") for j in jobs),
        },
    )


@app.get("/health")
async def health():
    async with db_session() as db:
        await db.execute("SELECT 1")
    return {"status": "ok"}


# ---------- JSON API ----------
@app.post("/add_project")
async def add_project(data: ProjectIn):
    async with db_session() as db:
        res = await db.execute(
            "INSERT INTO projects (name, description) VALUES (?, ?)",
            [data.name.strip(), data.description.strip()],
        )
    return {"status": "success", "id": res.last_insert_rowid}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int):
    async with db_session() as db:
        await db.execute("DELETE FROM master_plan WHERE project_id = ?", [project_id])
        await db.execute("DELETE FROM hints_memory WHERE project_id = ?", [project_id])
        await db.execute("DELETE FROM projects WHERE id = ?", [project_id])
    return {"status": "deleted"}


@app.post("/api/projects/{project_id}/plan")
async def request_plan(project_id: int):
    """Queue a job; worker.py on the laptop (Ollama) picks it up."""
    async with db_session() as db:
        rs = await db.execute("SELECT id FROM projects WHERE id = ?", [project_id])
        if not rs.rows:
            raise HTTPException(404, "Project not found")
    job_id = await _queue_job("generate_plan", {"project_id": project_id})
    return {"status": "queued", "job_id": job_id}


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, data: StatusIn):
    if data.status not in VALID_STATUS:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUS)}")
    async with db_session() as db:
        await db.execute("UPDATE master_plan SET status = ? WHERE id = ?", [data.status, task_id])
    return {"status": "updated"}


@app.post("/api/search")
async def api_search(data: SearchIn):
    """Gatekeeper runs on the cloud: search the web, clean, cache in Turso."""
    try:
        res = await search_and_cache(data.query, force=data.force)
    except GatekeeperError as e:
        raise HTTPException(502, str(e))
    return {
        "query": res["query"],
        "cached": res["cached"],
        "sources": res["sources"],
        "preview": res["clean_data"][:1500],
    }


@app.delete("/api/cache/{cache_id}")
async def delete_cache(cache_id: int):
    async with db_session() as db:
        await db.execute("DELETE FROM knowledge_cache WHERE id = ?", [cache_id])
    return {"status": "deleted"}


@app.delete("/api/hints/{hint_id}")
async def delete_hint(hint_id: int):
    async with db_session() as db:
        await db.execute("DELETE FROM hints_memory WHERE id = ?", [hint_id])
    return {"status": "deleted"}


@app.get("/api/jobs")
async def list_jobs():
    async with db_session() as db:
        return rows_to_dicts(await db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 20"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
