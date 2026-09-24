#!/usr/bin/env python3
"""One-time reconciliation: sync Auto/delete-candidate with the corrected predicate.
Removes the label from mails no longer eligible, adds it to newly eligible ones.
Safety: UIDVALIDITY-validated map, full UID coverage required, hard verification."""
import imaplib, os, re, sqlite3, ssl, sys, time

DB = os.environ.get("EC_DB", os.path.expanduser("~/email-classifier/mail.db"))
HOST = "imap.gmail.com"
LABEL = '"Auto/delete-candidate"'
COND = ("tier IN ('heuristic','jev') AND delete_safe>=0.85 AND confidence>=0.5 "
        "AND category NOT IN ('finance_legal','receipt','transactional')")

def token():
    import subprocess
    return subprocess.run(["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail_token.py")],
                          capture_output=True, text=True).stdout.strip()

ctx = ssl.create_default_context()
M = imaplib.IMAP4_SSL(HOST, ssl_context=ctx)
auth = "user=" + os.environ["GMAIL_USER"] + "\x01auth=Bearer " + token() + "\x01\x01"
typ, _ = M.authenticate("XOAUTH2", lambda x: auth.encode()); assert typ == "OK"

db = sqlite3.connect(DB, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS imap_state (k TEXT PRIMARY KEY, v TEXT)""")

# UIDVALIDITY guard: stale maps must never drive label removals
typ, data = M.status('"[Gmail]/All Mail"', "(UIDVALIDITY)"); assert typ == "OK", data
uv_new = re.search(rb"UIDVALIDITY (\d+)", data[0]).group(1).decode()
uv_old = db.execute("SELECT v FROM imap_state WHERE k='uidvalidity'").fetchone()
if not uv_old or uv_old[0] != uv_new:
    sys.exit(f"UID map not validated for current mailbox (uidvalidity {uv_new}, stored: {uv_old and uv_old[0]}). "
             "Run labeler.py first to (re)build the map, then rerun this.")

typ, _ = M.select('"[Gmail]/All Mail"', readonly=False); assert typ == "OK"

# current label membership: search UIDs having the label
typ, data = M.uid("search", None, f"X-GM-LABELS {LABEL}"); assert typ == "OK", data
have = {int(u) for u in data[0].split()}
print("currently labeled:", len(have))

# full coverage required: every UID must be mapped, else removal decisions are unsafe
all_uids = {int(u) for u in M.uid("search", None, "ALL")[1][0].split()}
mapped_uids = {int(u) for u, g in db.execute("SELECT uid, gid FROM imap_map")}
unmapped = all_uids - mapped_uids
if unmapped:
    sys.exit(f"{len(unmapped)} UIDs are not in imap_map; refusing to reconcile on partial data. "
             "Run labeler.py to complete the map first.")

# desired set from corrected predicate
rows = db.execute(f"SELECT gid FROM messages WHERE gid!='__meta__' AND {COND}").fetchall()
gids = {r[0][2:] if r[0].startswith("h:") else r[0] for r in rows}
uidmap = db.execute("SELECT uid, gid FROM imap_map").fetchall()
want = {u for u, g in uidmap if g in gids}
print("should be labeled:", len(want))

to_remove = have - want
to_add = want - have
print(f"to remove: {len(to_remove)}, to add: {len(to_add)}")

def chunks(s, n=500):
    l = sorted(s)
    for i in range(0, len(l), n):
        yield ",".join(map(str, l[i:i+n]))

for c in chunks(to_remove):
    typ, _ = M.uid("store", c, "-X-GM-LABELS", f"({LABEL})")
    assert typ == "OK", f"remove failed: {typ}"
    time.sleep(0.5)
for c in chunks(to_add):
    typ, _ = M.uid("store", c, "+X-GM-LABELS", f"({LABEL})")
    assert typ == "OK", f"add failed: {typ}"
    time.sleep(0.5)

# verify by re-reading live membership; mismatch is a hard failure
typ, data = M.uid("search", None, f"X-GM-LABELS {LABEL}"); assert typ == "OK", data
final = {int(u) for u in data[0].split()}
missing, extra = want - final, final - want
print("final labeled:", len(final), f"| missing: {len(missing)}, unexpected: {len(extra)}")
M.logout()
if missing or extra:
    sys.exit(f"RECONCILIATION INCOMPLETE: {len(missing)} missing, {len(extra)} unexpected — rerun.")
print("reconciled OK")
