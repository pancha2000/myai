import os
from dotenv import load_dotenv
import libsql_client

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


def get_db_client():
  """Turso Cloud Database සමඟ ආරක්ෂිත සම්බන්ධතාව ලබා දෙයි."""
  if not TURSO_URL or not TURSO_AUTH_TOKEN:
    raise ValueError(
        "❌ Turso URL හෝ Token .env ෆයිල් එකේ නිවැරදිව සඳහන් කර නැත!"
    )
  return libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_AUTH_TOKEN)


def init_database():
  """අවශ්‍ය සියලුම ඩේටාබේස් මේස (Tables) නිර්මාණය කරයි."""
  client = get_db_client()

  # 1. Projects Table
  client.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  # 2. Master Plan Table (Epic -> Task -> Sub-task Tree)
  client.execute("""
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
  client.execute("""
        CREATE TABLE IF NOT EXISTS hints_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            module_name TEXT,
            hint_content TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """)

  # 4. Knowledge Cache Table (ഇන්ටර්නෙට් ඩේටා සහ ඩොකියුමන්ට්ස් සේව් කිරීමට)
  client.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_keyword TEXT UNIQUE,
            clean_data TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  client.close()
  print("✅ Turso ඩේටාබේස් ව්‍යුහය සාර්ථකව සකස් විය!")


if __name__ == "__main__":
  init_database()
