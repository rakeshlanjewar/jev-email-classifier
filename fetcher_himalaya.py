#!/usr/bin/env python3
"""Fetch all envelopes via himalaya IMAP (fast, no API quota) into the same SQLite DB.
Primary fetch method; fetcher.py (Gmail API) is the fallback."""
import json, sqlite3, subprocess, sys, time
import os

DB = os.environ.get("EC_DB", os.path.expanduser("~/email-classifier/mail.db"))
FOLDER = "[Gmail]/All Mail"
PAGE = 200

db = sqlite3.connect(DB)
db.executescript("""
CREATE TABLE IF NOT EXISTS messages (
  gid TEXT PRIMARY KEY,
  from_h TEXT, subject TEXT, list_unsub TEXT, snippet TEXT,
  date TEXT, to_h TEXT,
  tier TEXT, category TEXT, delete_safe REAL, personal_value REAL,
  confidence REAL, raw_answers TEXT, classified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tier ON messages(tier);
""")
db.execute("INSERT OR REPLACE INTO messages (gid, from_h, subject, list_unsub, snippet, date, to_h)"
           " VALUES ('__meta__', NULL, NULL, NULL, NULL, NULL, NULL)")
db.commit()  # release the write lock before any network work

def run(p):
    r = subprocess.run(["himalaya", "envelope", "list", "-m", FOLDER, "--json",
                        "-s", str(PAGE), "-p", str(p)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return json.loads(r.stdout)["envelopes"]

# find highest page
lo, hi = 1, 4096
while lo < hi:
    mid = (lo + hi + 1) // 2
    try:
        if run(mid): lo = mid
        else: hi = mid - 1
    except Exception:
        hi = mid - 1
print(f"{FOLDER}: {lo} pages", flush=True)

total = 0
for p in range(1, lo + 1):
    for e in run(p):
        mid = (e.get("message-id") or e["id"]).strip()
        f = "; ".join(f"{x.get('name')} <{x.get('email')}>" for x in (e.get("from") or []))
        t = "; ".join(x.get("email", "") for x in (e.get("to") or []))
        db.execute("INSERT OR IGNORE INTO messages (gid,from_h,subject,list_unsub,snippet,date,to_h) VALUES (?,?,?,?,?,?,?)",
                   ("h:" + mid, f, e.get("subject"), "", "", e.get("date"), t))
    db.commit()
    total += PAGE
    if p % 10 == 0: print(f"page {p}/{lo} (~{min(total, lo*PAGE)})", flush=True)
print("DONE", flush=True)
