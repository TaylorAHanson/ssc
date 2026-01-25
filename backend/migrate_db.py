
import sqlite3
import os

# Path to DB
db_path = "backend/edas_hub.db"

if not os.path.exists(db_path):
    print(f"Database {db_path} not found. Skipping migration (will be created fresh).")
    exit(0)

print(f"Migrating {db_path}...")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check if parent_id exists
cols = [row[1] for row in cursor.execute("PRAGMA table_info(requests)")]
if "parent_id" not in cols:
    print("Adding parent_id column...")
    cursor.execute("ALTER TABLE requests ADD COLUMN parent_id TEXT")
else:
    print("parent_id already exists.")

if "root_id" not in cols:
    print("Adding root_id column...")
    cursor.execute("ALTER TABLE requests ADD COLUMN root_id TEXT")
else:
    print("root_id already exists.")

if "conversation" not in cols:
    print("Adding conversation column...")
    cursor.execute("ALTER TABLE requests ADD COLUMN conversation JSON")
else:
    print("conversation already exists.")
    
conn.commit()
conn.close()
print("Migration complete.")
