"""Laptop worker: polls Turso for jobs queued from the Koyeb dashboard.

    python worker.py                 # loop forever
    python worker.py --once          # process pending jobs and exit
    python worker.py plan 1          # generate plan for project id 1 now
    python worker.py search "query"  # research a topic now
"""
import argparse
import asyncio
import json
import traceback

from core.db import db_session, init_database, rows_to_dicts
from core.gatekeeper import search_and_cache
from core.orchestrator import generate_master_plan

POLL_SECONDS = 5


async def _set_job(job_id: int, status: str, result: str = ""):
    async with db_session() as db:
        await db.execute(
            "UPDATE jobs SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            [status, result[:2000], job_id],
        )


async def _claim_job():
    async with db_session() as db:
        rs = await db.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
        )
        rows = rows_to_dicts(rs)
        if not rows:
            return None
        job = rows[0]
        upd = await db.execute(
            "UPDATE jobs SET status = 'running', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            [job["id"]],
        )
        return job if upd.rows_affected == 1 else None


async def run_job(job: dict):
    payload = json.loads(job.get("payload") or "{}")
    kind = job["job_type"]
    print(f"[job {job['id']}] {kind} {payload}")
    try:
        if kind == "generate_plan":
            res = await generate_master_plan(int(payload["project_id"]))
            await _set_job(job["id"], "done",
                           f"{res['tasks']} tasks, {res['hints']} hints saved")
        elif kind == "research":
            res = await search_and_cache(payload["query"], force=bool(payload.get("force")))
            await _set_job(job["id"], "done",
                           f"{'cache' if res['cached'] else 'web'}: {len(res['sources'])} sources")
        else:
            await _set_job(job["id"], "error", f"unknown job type: {kind}")
    except Exception as e:
        traceback.print_exc()
        await _set_job(job["id"], "error", str(e))


async def loop(once: bool):
    await init_database()
    # jobs left 'running' by a crashed worker go back to the queue
    async with db_session() as db:
        await db.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
    print("Worker started. Waiting for jobs...")
    while True:
        job = await _claim_job()
        if job:
            await run_job(job)
            continue
        if once:
            return
        await asyncio.sleep(POLL_SECONDS)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", choices=["plan", "search"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if args.command == "plan":
        await init_database()
        print(await generate_master_plan(int(args.arg)))
    elif args.command == "search":
        await init_database()
        res = await search_and_cache(args.arg, force=True)
        print(res["clean_data"][:3000])
    else:
        await loop(args.once)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("bye")
