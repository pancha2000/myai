from contextlib import asynccontextmanager
import os
from core.db import get_db_client, init_database
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates


@asynccontextmanager
async def lifespan(app: FastAPI):
  # Event loop එක සක්‍රීය වූ පසු ඩේටාබේස් එක සාදයි
  await init_database()
  yield


app = FastAPI(title="Local Coding Agent Storage Portal", lifespan=lifespan)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
  client = await get_db_client()

  projects_res = await client.execute(
      "SELECT * FROM projects ORDER BY id DESC"
  )
  projects = [
      dict(zip(projects_res.columns, row)) for row in projects_res.rows
  ]

  plans_res = await client.execute("SELECT * FROM master_plan ORDER BY id DESC")
  plans = [dict(zip(plans_res.columns, row)) for row in plans_res.rows]

  hints_res = await client.execute(
      "SELECT * FROM hints_memory ORDER BY id DESC"
  )
  hints = [dict(zip(hints_res.columns, row)) for row in hints_res.rows]

  cache_res = await client.execute(
      "SELECT * FROM knowledge_cache ORDER BY id DESC"
  )
  cache = [dict(zip(cache_res.columns, row)) for row in cache_res.rows]

  await client.close()

  return templates.TemplateResponse(
      "index.html",
      {
          "request": request,
          "projects": projects,
          "plans": plans,
          "hints": hints,
          "cache": cache,
      },
  )


@app.post("/add_project")
async def add_project(name: str = Form(...), description: str = Form(...)):
  client = await get_db_client()
  await client.execute(
      "INSERT INTO projects (name, description) VALUES (?, ?)",
      [name, description],
  )
  await client.close()
  return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 8000))
  import uvicorn

  uvicorn.run("main:app", host="0.0.0.0", port=port)
