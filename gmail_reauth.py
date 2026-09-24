#!/usr/bin/env python3
"""One-time Gmail OAuth re-consent. Prints an auth URL, redeems the pasted
redirect URL's code (PKCE verifier saved to /tmp/oauth_state.json), saves token.
Scope is the full https://mail.google.com/ — required for Gmail IMAP XOAUTH2."""
import json, urllib.parse
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://mail.google.com/"]
CLIENT = os.environ.get("EC_CLIENT_SECRET", os.path.expanduser("~/.hermes/google_client_secret.json"))
TOKEN_OUT = os.environ.get("EC_TOKEN", os.path.expanduser("~/.hermes/google_token.json"))

cs = json.load(open(CLIENT))["installed"]
flow = InstalledAppFlow.from_client_config({**{"installed": {**cs, "redirect_uris": ["http://localhost:8899/"]}}}, SCOPES)
flow.redirect_uri = "http://localhost:8899/"  # must be explicit; client JSON alone doesn't set it
url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="false",
                                state="manual-consent")
json.dump({"code_verifier": flow.code_verifier}, open("/tmp/oauth_state.json", "w"))
print(url)
code = input("paste the redirect URL (or just the code): ").strip()
if "code=" in code:
    code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)["code"][0]
flow.fetch_token(code=code)
t = flow.credentials
json.dump({"token": t.token, "refresh_token": t.refresh_token, "token_uri": t.token_uri,
           "client_id": t.client_id, "client_secret": t.client_secret, "scopes": t.scopes,
           "type": "authorized_user"}, open(TOKEN_OUT, "w"))
print("token saved, scope:", t.scopes)
