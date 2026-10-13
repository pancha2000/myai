# myai – Local Coding Agent

* **Koyeb (cloud):** `main.py` – dashboard + internet search/cache (gatekeeper) → Turso
* **Laptop (local):** `worker.py` – picks up jobs from Turso, runs Ollama (7B), writes plans back

Koyeb cannot reach `localhost:11434` on your laptop, so the dashboard puts "Generate plan"
requests into the `jobs` table and `worker.py` on your laptop processes them.

## Laptop
    pip install -r requirements.txt
    cp .env.example .env   # fill in values
    ollama pull qwen2.5:7b
    python worker.py              # keeps polling Turso
    python worker.py plan 1       # or run directly for project id 1
    python worker.py search "fastapi lifespan"

## Koyeb
Deploy from GitHub (Dockerfile). Set env vars: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN,
ADMIN_USER, ADMIN_PASSWORD. Do NOT commit `.env`.
