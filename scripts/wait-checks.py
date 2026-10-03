"""Require successful checks at the exact commit before using signing credentials."""
import json
import os
import re
import time
import urllib.error
import urllib.request

sha = os.environ['YY_SHA']
repo = os.environ['GITHUB_REPOSITORY']
if not re.fullmatch(r'[0-9a-f]{40}', sha) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
    raise SystemExit('Invalid build commit/repository')
url = f'https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=latest'
deadline = time.monotonic() + 900
required = {'automation', 'windows', 'ios'}
while time.monotonic() < deadline:
    request = urllib.request.Request(url, headers={'Authorization': f"Bearer {os.environ['GH_TOKEN']}", 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            checks = json.load(response)['check_runs']
    except urllib.error.HTTPError as error:
        if error.code < 500:
            raise SystemExit(f'Cannot read required checks: HTTP {error.code}')
        time.sleep(20)
        continue
    if any(c['name'] in required and c['status'] == 'completed' and c['conclusion'] != 'success' for c in checks):
        raise SystemExit('Required checks failed; signing and upload will not run')
    passed = {c['name'] for c in checks if c['status'] == 'completed' and c['conclusion'] == 'success'}
    if required <= passed:
        print('All required checks passed at', sha)
        break
    time.sleep(20)
else:
    raise SystemExit('Timed out waiting for checks; retry after inspecting the Checks workflow')
