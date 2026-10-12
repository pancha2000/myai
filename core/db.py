import os
from dotenv import load_dotenv
import libsql_client

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


async def get_db_client():
  """Async කනෙක්ෂන් එකක් ලබා දෙයි."""
  if not TURSO_URL or not TURSO_AUTH_TOKEN:
    raise ValueError(
        "❌ Turso URL හෝ Token .env / Environment Variables වල නැත!"
    )

  # Sync වෙනුවට async client එක භාවිත කරයි
  return libsql_client.create_client_async(
      url=TURSO_URL, auth_token=TURSO_AUTH_TOKEN
  )


async def init_database():
  """අවශ්‍ය සියලුම Tables async ලෙස ස්වයංක්‍රීයව සාදයි."""
  client = await get_db_client()

  # 1. Projects Table
  await client.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  # 2. Master Plan Table
  await client.execute("""
        CREATE TABLE IF NOT EXISTS master_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            parent_id INTEGER,
            task_title TEXT NOT NULL,
            task_description TEXT,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """)

  # 3. Hints Memory Table
  await client.execute("""
        CREATE TABLE IF NOT EXISTS hints_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            module_name TEXT,
            hint_content TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """)

  # 4. Knowledge Cache Table
  await client.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_keyword TEXT UNIQUE,
            clean_data TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  await client.close()
  print("✅ Turso ඩේටාබේස් ව්‍යුහය සාර්ථකව සකස් විය!")
