#!/usr/bin/env python3
"""Print a fresh Gmail access token from the stored OAuth token (refreshes if needed)."""
import json
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN = os.environ.get("EC_TOKEN", os.path.expanduser("~/.hermes/google_token.json"))
t = json.load(open(TOKEN))
c = Credentials.from_authorized_user_info(t)
if c.expired or not c.valid:
    c.refresh(Request())
    json.dump(json.loads(c.to_json()), open(TOKEN, "w"))
print(c.token)
