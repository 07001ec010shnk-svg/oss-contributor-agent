from __future__ import annotations
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .config import ROOT

DB_PATH = ROOT / "agent.db"

@contextmanager
def connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init():
    with connection() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS repos (
            full_name TEXT PRIMARY KEY, url TEXT, language TEXT, score REAL,
            external_merged INTEGER, closed_sample INTEGER, median_merge_days REAL,
            merged_30d INTEGER, last_push TEXT, policy TEXT DEFAULT 'unreviewed',
            policy_notes TEXT DEFAULT '', updated REAL
        );
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY, repo TEXT, number INTEGER, title TEXT,
            body TEXT, url TEXT, state TEXT DEFAULT 'discovered', fit_score REAL DEFAULT 0,
            analysis TEXT DEFAULT '', workdir TEXT DEFAULT '', diff_path TEXT DEFAULT '',
            test_result TEXT DEFAULT '', branch TEXT DEFAULT '',
            pr_number INTEGER, pr_url TEXT, review_state TEXT DEFAULT '', last_error TEXT DEFAULT '',
            updated REAL,
            UNIQUE(repo, number)
        );
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, provider TEXT, model TEXT,
            request_tokens INTEGER, response_tokens INTEGER, success INTEGER,
            error_code TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(provider, ts);
        CREATE TABLE IF NOT EXISTS api_state (
            name TEXT PRIMARY KEY, value TEXT, updated REAL
        );
        """)
        existing={r[1] for r in c.execute("PRAGMA table_info(issues)")}
        if "review_state" not in existing:
            c.execute("ALTER TABLE issues ADD COLUMN review_state TEXT DEFAULT ''")
        if "ai_review" not in existing:
            c.execute("ALTER TABLE issues ADD COLUMN ai_review TEXT DEFAULT ''")
        if "submitted_at" not in existing:
            c.execute("ALTER TABLE issues ADD COLUMN submitted_at REAL")

def rows(sql: str, args=()):
    with connection() as c:
        return [dict(row) for row in c.execute(sql, args).fetchall()]

def one(sql: str, args=()):
    found = rows(sql, args)
    return found[0] if found else None

def execute(sql: str, args=()):
    with connection() as c:
        c.execute(sql, args)

def store_repo(r):
    with connection() as c:
        c.execute("""INSERT INTO repos(full_name,url,language,score,external_merged,closed_sample,
         median_merge_days,merged_30d,last_push,updated)
         VALUES(:full_name,:url,:language,:score,:external_merged,:closed_sample,
         :median_merge_days,:merged_30d,:last_push,:updated)
         ON CONFLICT(full_name) DO UPDATE SET
         url=excluded.url,language=excluded.language,score=excluded.score,
         external_merged=excluded.external_merged,closed_sample=excluded.closed_sample,
         median_merge_days=excluded.median_merge_days,merged_30d=excluded.merged_30d,
         last_push=excluded.last_push,updated=excluded.updated""", r)

def store_issue(i):
    with connection() as c:
        c.execute("""INSERT INTO issues(id,repo,number,title,body,url,updated)
        VALUES(:id,:repo,:number,:title,:body,:url,:updated)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,url=excluded.url,
        updated=excluded.updated""", i)

def update_issue(issue_id, **fields):
    allowed = {"state", "fit_score", "analysis", "workdir", "diff_path", "test_result",
               "branch", "pr_number", "pr_url", "review_state", "last_error",
               "ai_review", "submitted_at"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    if not payload:
        return
    payload["updated"] = time.time()
    params = list(payload.values()) + [int(issue_id)]
    execute("UPDATE issues SET " + ",".join(f"{k}=?" for k in payload) + " WHERE id=?", params)

def usage(provider, model, input_tokens, output_tokens, ok, error_code=""):
    execute("INSERT INTO usage(ts,provider,model,request_tokens,response_tokens,success,error_code) VALUES(?,?,?,?,?,?,?)",
            (time.time(), provider, model, int(input_tokens), int(output_tokens), int(ok), str(error_code)[:80]))

def limit_totals(provider, window_seconds):
    return one("""SELECT COUNT(*) AS requests,COALESCE(SUM(request_tokens),0) AS input_tokens,
      COALESCE(SUM(response_tokens),0) AS output_tokens FROM usage
      WHERE provider=? AND ts>=?""", (provider, time.time()-window_seconds))
