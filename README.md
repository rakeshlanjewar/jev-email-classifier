# email-classifier

Bulk Gmail cleanup pipeline: fetch → classify → label. **Never deletes anything** — classification and labels only, so you can review before removing anything yourself.

Two-tier classification keeps cost at ~$0.65 for a 60k-message mailbox:

- **Tier 0** — free heuristics: `List-Unsubscribe` header, noreply/OTP/automated sender patterns → labeled with zero API calls.
- **Tier 1 — Jev** (TypeSafe AI System One model): typed questions per email, evaluated in parallel —
  - `Choice` over 12 categories (newsletter, promo, receipt, finance-legal, work, personal, …)
  - `Noul` — calibrated probability "safe to delete without loss?"
  - `Confidence` — anything < 0.5 goes to a human-review label, never auto-labeled.
- **Hard overrides** — finance/legal senders are force-protected regardless of model output.

Labels are applied back to Gmail additively via IMAP `X-GM-LABELS` (never removes existing labels).

## Setup

### Google Cloud OAuth setup (one-time)

Gmail IMAP XOAUTH2 needs an OAuth token, which needs a GCP OAuth client:

1. [console.cloud.google.com](https://console.cloud.google.com) → create/select a project.
2. **APIs & Services → Library** → enable **Gmail API**.
3. **APIs & Services → OAuth consent screen** → External → fill app name + your email →
   add your own account under **Test users** (unverified apps in testing mode work fine for personal use;
   the per-minute API quota is small, but this pipeline uses IMAP, which bypasses it).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   Application type **Desktop app** → download the JSON as `google_client_secret.json`.
5. Consent with the full mail scope (narrow `gmail.*` scopes are rejected by Gmail IMAP):

   ```bash
   python3 gmail_reauth.py   # prints a URL; open it, approve, token lands in google_token.json
   ```

   `gmail_token.py` refreshes that token automatically thereafter.

### himalaya CLI

```bash
# Install (Linux/macOS)
curl -sSL https://raw.githubusercontent.com/pimalaya/himalaya/master/install.sh | sh

# Config: ~/.config/himalaya/config.toml — IMAP via XOAUTH2 using our token helper
cat > ~/.config/himalaya/config.toml <<'EOF'
[accounts.gmail]
default = true
email = "you@gmail.com"

imap.server = "imaps://imap.gmail.com:993"
imap.sasl.xoauth2.username = "you@gmail.com"
imap.sasl.xoauth2.token.command = ["python3", "/path/to/gmail_token.py"]
mailbox.alias.inbox = "INBOX"
mailbox.alias.archive = "[Gmail]/All Mail"
EOF

# Verify
himalaya account check && himalaya mailbox list
```

Notes: the token must carry the full `https://mail.google.com/` scope (narrower
gmail.* scopes are rejected by Gmail IMAP with XOAUTH2). himalaya's `gmail`
REST backend shares Gmail API quota; the IMAP backend does not — that's why we
use IMAP.

**Alternative: app password (SASL PLAIN).** If you'd rather skip OAuth entirely,
enable 2-step verification on the Google account and create an app password
(myaccount.google.com/apppasswords), then:

```toml
[accounts.gmail]
imap.server = "imaps://imap.gmail.com:993"
imap.sasl.plain.username = "you@gmail.com"
imap.sasl.plain.password.command = "pass show gmail"   # any command printing the password
mailbox.alias.inbox = "INBOX"
mailbox.alias.sent = "[Gmail]/Sent Mail"
mailbox.alias.drafts = "[Gmail]/Drafts"
mailbox.alias.trash = "[Gmail]/Trash"
mailbox.alias.archive = "[Gmail]/All Mail"
```

Every Gmail label shows up as a top-level IMAP mailbox; special ones live
under the `[Gmail]/` prefix, so quote them in the shell or alias them —
`[Gmail]/All Mail` is the everything-archive this pipeline fetches from.
Note: Gmail enforces small per-day IMAP download limits, more than enough here.

### TypeSafe (Jev) API key

1. Sign up at [console.typesafe.ai](https://console.typesafe.ai).
2. **API Keys → Create key** → copy it (`apikey_...`).
3. Keep it out of the repo: `export TYPESAFE_API_KEY="..."` in `~/.bashrc` or your secret manager.

### Python + keys

```bash
pip install -r requirements.txt

# OAuth: create GCP OAuth client (Desktop app), put google_client_secret.json aside,
# generate a token with scope https://mail.google.com/ (see gmail_reauth.py pattern)
export TYPESAFE_API_KEY="..."   # console.typesafe.ai
export GMAIL_USER="you@gmail.com"  # account used by labeler.py / reconcile.py

# Optional path overrides (defaults shown)
# EC_DB=~/email-classifier/mail.db  EC_TOKEN=~/.hermes/google_token.json
# EC_CLIENT_SECRET=~/.hermes/google_client_secret.json
```

Files expected (never committed):
- `~/.hermes/google_token.json` — OAuth token
- `~/.hermes/google_client_secret.json` — OAuth client
- `~/.config/himalaya/config.toml` — himalaya IMAP account
- `mail.db`, `review.csv` — generated locally

## Usage

```bash
# 1. Fetch: creates mail.db (SQLite) with schema on first run, then pulls all
#    envelopes from All Mail via himalaya IMAP. Resumable (dedupe on message-id).
python3 fetcher_himalaya.py     # himalaya IMAP, resumable, no API quota

# 2. Classify: Tier 0 heuristics + Jev for the rest
python3 classify.py

# 3. Review
#    review.csv — delete candidates sorted by confidence
#    sqlite3 mail.db "SELECT category, COUNT(*), AVG(delete_safe) FROM messages GROUP BY 1;"

# 4. Apply labels additively via IMAP
python3 labeler.py
```

Resulting Gmail labels: `Auto/delete-candidate`, `Auto/newsletter`, `Auto/promo`, `Auto/notification`, `Keep/finance-legal`, `Keep/review-lowconf`.

## Files

| File | Purpose |
| --- | --- |
| `fetcher_himalaya.py` | Fetch envelopes via himalaya IMAP (no Gmail API quota) |
| `classify.py` | Tier 0 heuristics + Jev classification, checkpointed |
| `labeler.py` | Apply labels via IMAP X-GM-LABELS (additive only) |
| `gmail_token.py` | Print/refresh the OAuth access token |
| `gmail_reauth.py` | One-time OAuth re-consent helper |
