import json,sqlite3
from pathlib import Path

def init_db(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as con:con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL)");con.commit()

def save_result(path,result):
    init_db(path); payload=json.dumps(result,ensure_ascii=False)
    with sqlite3.connect(path) as con:con.execute("INSERT INTO state(key,value) VALUES('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(payload,));con.commit()

def load_result(path):
    init_db(path)
    with sqlite3.connect(path) as con:row=con.execute("SELECT value FROM state WHERE key='latest'").fetchone()
    return json.loads(row[0]) if row else None
