"""
One-off local diagnostic for the repository_dispatch 404. Never prints the
token itself - only what GitHub's API says about it.

Run with: venv310\\Scripts\\python.exe scripts\\check_github_access.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

token = os.environ.get("GITHUB_PAT")
repo = os.environ.get("GITHUB_REPO")

print(f"GITHUB_REPO as loaded: {repo!r}")
print(f"GITHUB_PAT present: {bool(token)} (length: {len(token) if token else 0})")

if not token or not repo:
    print("\n[FAIL] One or both env vars are missing/empty. Fix .env and re-run.")
    sys.exit(1)

import requests

headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {token}",
}

# 1. Who does this token authenticate as?
me = requests.get("https://api.github.com/user", headers=headers, timeout=15)
print(f"\n[/user] status={me.status_code}")
if me.status_code == 200:
    print(f"  Authenticated as: {me.json().get('login')}")
else:
    print(f"  Body: {me.text}")

# 2. Can this token see the target repo, and what access does it have?
repo_resp = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=15)
print(f"\n[/repos/{repo}] status={repo_resp.status_code}")
if repo_resp.status_code == 200:
    perms = repo_resp.json().get("permissions", {})
    print(f"  Repo visible. Permissions on it: {perms}")
else:
    print(f"  Body: {repo_resp.text}")

# 3. Classic PAT scopes (only present for classic tokens, not fine-grained)
scopes_header = me.headers.get("X-OAuth-Scopes")
print(f"\nClassic PAT scopes header: {scopes_header!r} "
      f"(None is expected/normal for fine-grained tokens)")

# 4. The actual call that's failing
dispatch_resp = requests.post(
    f"https://api.github.com/repos/{repo}/dispatches",
    headers=headers,
    json={"event_type": "diagnostic-test"},
    timeout=15,
)
print(f"\n[POST /repos/{repo}/dispatches] status={dispatch_resp.status_code}")
print(f"  Body: {dispatch_resp.text}")
if dispatch_resp.status_code == 204:
    print("\n[OK] Dispatch succeeded! Check the Actions tab for a run.")
else:
    print("\n[FAIL] See status/body above.")
