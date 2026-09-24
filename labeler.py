#!/usr/bin/env python3
"""Apply classification labels to Gmail via IMAP X-GM-LABELS. Adds labels only, never removes."""
import imaplib, json, os, re, sqlite3, ssl, time

DB = os.environ.get("EC_DB", os.path.expanduser("~/email-classifier/mail.db"))
HOST = "imap.gmail.com"

def token():
    import subprocess
    return subprocess.run(["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail_token.py")],
                          capture_output=True, text=True).stdout.strip()

LABELS = {  # target label -> SQL condition
    # precedence: never label delete-candidate on protected/keep categories or low confidence
    '"Auto/delete-candidate"': "tier IN ('heuristic','jev') AND delete_safe>=0.85 AND confidence>=0.5"
                               " AND category NOT IN ('finance_legal','receipt','transactional')",
    '"Auto/newsletter"': "category='newsletter'",
    '"Auto/promo"': "category='promo'",
    '"Auto/notification"': "category='notification' AND delete_safe<0.85",
    '"Keep/finance-legal"': "category IN ('finance_legal','receipt','transactional') OR tier='protected'",
    '"Keep/review-lowconf"': "tier='jev' AND confidence<0.5",
}

def conn():
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(HOST, ssl_context=ctx)
    auth = "user=" + os.environ["GMAIL_USER"] + "\x01auth=Bearer " + token() + "\x01\x01"
    typ, _ = M.authenticate("XOAUTH2", lambda x: auth.encode())
    assert typ == "OK", typ
    return M

def main():
    db = sqlite3.connect(DB, check_same_thread=False)
    db.execute("""CREATE TABLE IF NOT EXISTS imap_map (uid INTEGER PRIMARY KEY, gid TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS imap_state (k TEXT PRIMARY KEY, v TEXT)""")
    M = conn()
    typ, data = M.status('"[Gmail]/All Mail"', "(UIDVALIDITY)"); assert typ == "OK"
    uv_new = re.search(rb"UIDVALIDITY (\d+)", data[0]).group(1).decode()
    uv_old = db.execute("SELECT v FROM imap_state WHERE k='uidvalidity'").fetchone()
    if uv_old and uv_old[0] != uv_new:
        print(f"UIDVALIDITY changed {uv_old[0]} -> {uv_new}: clearing stale UID map", flush=True)
        db.execute("DELETE FROM imap_map")
        db.execute("INSERT OR REPLACE INTO imap_state VALUES ('uidvalidity', ?)", (uv_new,))
        db.commit()
    elif not uv_old:
        db.execute("INSERT OR REPLACE INTO imap_state VALUES ('uidvalidity', ?)", (uv_new,))
        db.commit()
    typ, _ = M.select('"[Gmail]/All Mail"', readonly=False)
    assert typ == "OK"
    n_msgs = int(M.select('"[Gmail]/All Mail"', readonly=False)[1][0])
    print("all mail count:", n_msgs, flush=True)

    for lbl in LABELS:
        M.create(lbl)  # no-op if exists

    # pass 1: map uid -> gid (resumable: only fetch UIDs not yet in imap_map)
    mapped = {r[0] for r in db.execute("SELECT uid FROM imap_map")}
    uids = M.uid("search", None, "ALL")[1][0].split()
    to_map = [u for u in uids if int(u) not in mapped]
    print(f"uids: {len(uids)}, to map: {len(to_map)}", flush=True)
    CH = 500
    for i in range(0, len(to_map), CH):
        chunk = b",".join(to_map[i:i+CH])
        typ, data = M.uid("fetch", chunk, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        assert typ == "OK", (typ, data)
        for item in data:
            if isinstance(item, tuple):
                mu = re.search(rb"UID (\d+)", item[0])
                uid = int(mu.group(1))
                raw = item[1].decode(errors="replace")
                mid = ""
                for line in raw.splitlines():
                    if line.lower().startswith("message-id:"):
                        mid = line.split(":", 1)[1].strip().strip("<>")
                db.execute("INSERT OR REPLACE INTO imap_map VALUES (?,?)", (uid, mid or f"uid:{uid}"))
        db.commit()
        if (i // CH) % 20 == 0: print(f"mapped {i+len(to_map[i:i+CH])}/{len(to_map)}", flush=True)
    print("mapping done:", db.execute("SELECT COUNT(*) FROM imap_map").fetchone()[0], flush=True)

    # pass 2: apply labels
    for lbl, cond in LABELS.items():
        rows = db.execute(f"SELECT gid FROM messages WHERE gid!='__meta__' AND {cond}").fetchall()
        gids = {r[0][2:] if r[0].startswith("h:") else r[0] for r in rows}
        m = db.execute("SELECT uid, gid FROM imap_map").fetchall()
        targets = [str(u) for u, g in m if g in gids]
        print(f"{lbl}: {len(targets)} msgs", flush=True)
        for i in range(0, len(targets), 500):
            chunk = ",".join(targets[i:i+500])
            typ, _ = M.uid("store", chunk, "+X-GM-LABELS", f"({lbl})")
            assert typ == "OK", f"STORE failed for {lbl} chunk {i}: {typ}"
            time.sleep(0.5)
        print(f"  done {lbl}", flush=True)
    M.logout(); print("DONE", flush=True)

if __name__ == "__main__":
    main()
