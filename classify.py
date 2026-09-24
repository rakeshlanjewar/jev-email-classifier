#!/usr/bin/env python3
"""Classify emails: Tier 0 free heuristics, then Jev for the rest. Resumable."""
import json, os, re, sqlite3, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib import request as urlreq

DB = os.path.expanduser("~/email-classifier/mail.db")
KEY = os.environ.get("TYPESAFE_API_KEY")
if not KEY:
    # parse bashrc: `export TYPESAFE_API_KEY="..."` (tolerates spacing/comments)
    _bashrc = os.path.expanduser("~/.bashrc")
    if os.path.exists(_bashrc):
        _m = re.search(r'^export\s+TYPESAFE_API_KEY\s*=\s*["\']?([^"\'\s]+)', open(_bashrc).read(), re.M)
        if _m: KEY = _m.group(1)
if not KEY:
    sys.exit("TYPESAFE_API_KEY not set (env var or ~/.bashrc export)")
API = "https://api.typesafe.ai/v1/systemone"

CATEGORIES = {
 "newsletter": "Bulk digest, marketing, or subscribed content",
 "notification": "Automated alert: OTP, security, social, CI, system",
 "receipt": "Order confirmation, invoice, transaction record",
 "transactional": "Account/service action: password reset, verification, shipping update",
 "work": "Work-related: colleague, client, job, professional thread",
 "finance_legal": "Bank, tax, government, legal, insurance — keep",
 "travel": "Booking, ticket, itinerary",
 "personal": "Written by a human specifically to the recipient",
 "calendar": "Invite or event update",
 "promo": "Advertising / sales offer, not subscribed",
 "other": "Anything else",
}

FINANCE_RE = re.compile(r"(bank|hdfc|icici|sbi|axis|kotak|paytm|income.?tax|gst|maha.?rera|rera|court|legal|insurance|epfo|pf\b)", re.I)

def state_for(m):
    return json.dumps({
        "from": m["from_h"], "to": (m["to_h"] or "")[:200], "subject": m["subject"],
        "date": m["date"], "has_list_unsubscribe": bool(m["list_unsub"]),
        "snippet": (m["snippet"] or "")[:400]}, ensure_ascii=False)

def jev(state):
    questions = {
        "category": {"type": "choice", "instructions": "What kind of email is this?", "criteria": CATEGORIES},
        "delete_safe": {"type": "noul", "instructions": "Deleting this email loses nothing of lasting value (no records, obligations, info the recipient may need later)"},
        "personal_value": {"type": "noul", "instructions": "A human wrote this specifically to the recipient"},
    }
    body = json.dumps({"state": state, "model": "jev-latest", "questions": questions}).encode()
    for i in range(6):
        req = urlreq.Request(API, data=body, headers={
            "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
        try:
            resp = urlreq.urlopen(req, timeout=60)
            return json.load(resp)
        except urlreq.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise  # permanent client error: retrying won't help
            ra = e.headers.get("Retry-After", "")
            try:
                wait = max(0, min(120, int(ra)))
            except ValueError:
                wait = 5 * (i + 1)  # date-form or malformed header: fall back
            if i == 5: raise
            time.sleep(wait)
        except Exception:
            if i == 5: raise
            time.sleep(5 * (i + 1))

def tier0(m):
    """Returns (tier, category, delete_safe, confidence) or None."""
    if m["list_unsub"]:
        return ("heuristic", "newsletter", 0.95, 0.9)
    f = (m["from_h"] or "").lower()
    if re.search(r"(no-?reply|donotreply|notifications?@|noreply)", f):
        return ("heuristic", "notification", 0.85, 0.8)
    if re.search(r"(otp|one.?time|verification code|verify your)", (m["subject"] or ""), re.I):
        return ("heuristic", "notification", 0.9, 0.85)
    return None

def classify_one(m):
    st = state_for(m)
    r = jev(st)
    a = r["answers"]
    cat = a["category"]["choice"]; conf = a["category"]["confidence"]
    ds = a["delete_safe"]["noul"]; pv = a["personal_value"]["noul"]
    return ("jev", cat, ds, pv, conf, json.dumps(a))

def export_review(db):
    import csv
    def safe(v):  # neutralize spreadsheet formula injection
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from", "subject", "date", "category", "tier", "delete_safe", "confidence", "message_id"])
        for r in db.execute(
            "SELECT from_h, subject, date, category, tier, delete_safe, confidence, gid "
            "FROM messages WHERE gid!='__meta__' AND delete_safe>=0.85 AND confidence>=0.5 "
            "AND category NOT IN ('finance_legal','receipt','transactional') AND tier IN ('heuristic','jev') "
            "ORDER BY confidence DESC"):
            w.writerow(safe(v) if isinstance(v, str) else v for v in r)
    print("review.csv written:", out, flush=True)

def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
    db = sqlite3.connect(DB, check_same_thread=False)
    lock = __import__("threading").Lock()
    rows = db.execute(
        "SELECT gid, from_h, subject, list_unsub, snippet, date, to_h FROM messages WHERE tier IS NULL AND gid!='__meta__'"
    ).fetchall()[:limit]
    print(f"to classify: {len(rows)}", flush=True)
    # hard protection FIRST: finance/legal never reaches heuristics or Jev
    def protected(m):
        f = (m["from_h"] or "") + " " + (m["subject"] or "")
        return bool(FINANCE_RE.search(f))
    # Tier 0
    jev_rows = []
    for r in rows:
        m = dict(zip(["gid","from_h","subject","list_unsub","snippet","date","to_h"], r))
        if protected(m):
            with lock:
                db.execute("UPDATE messages SET tier=?,category=?,delete_safe=?,confidence=? WHERE gid=?",
                           ("protected", "finance_legal", 0.0, 1.0, m["gid"]))
            continue
        t = tier0(m)
        if t:
            with lock:
                db.execute("UPDATE messages SET tier=?,category=?,delete_safe=?,confidence=? WHERE gid=?",
                           (t[0], t[1], t[2], t[3], m["gid"]))
        else:
            jev_rows.append(m)
    db.commit()
    print(f"tier0 labeled, jev queue: {len(jev_rows)}", flush=True)

    done = 0
    errors = 0
    def work(m):
        nonlocal done, errors
        try:
            tier, cat, ds, pv, conf, raw = classify_one(m)
        except Exception as e:
            errors += 1
            print("ERR", m["gid"], e, flush=True); return
        f = m["from_h"] or ""
        if FINANCE_RE.search(f) or FINANCE_RE.search(m["subject"] or ""):
            tier, ds, conf = "protected", 0.0, 1.0  # hard override: never delete
        with lock:
            db.execute("UPDATE messages SET tier=?,category=?,delete_safe=?,personal_value=?,confidence=?,raw_answers=?,classified_at=? WHERE gid=?",
                       (tier, cat, ds, pv, conf, raw, datetime.now(timezone.utc).isoformat(), m["gid"]))
            db.commit()
        done += 1
        if done % 100 == 0: print(f"jev done: {done}", flush=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, jev_rows))
    export_review(db)
    print(f"DONE classified {done}, errors {errors}")
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
