#!/bin/bash
# A small Python web app with the SDLC enabled. The feature branch passes a query parameter
# to a shell command.
set -euo pipefail
mkdir -p app tests
cat > sdlc.config.json <<'EOF'
{
  "verify": ["python3 -m unittest discover -s tests -t ."]
}
EOF
printf '.sdlc/\n__pycache__/\n' > .gitignore
: > app/__init__.py
: > tests/__init__.py
cat > app/server.py <<'EOF'
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
EOF
cat > tests/test_server.py <<'EOF'
import unittest

from app import server


class ServerTest(unittest.TestCase):
    def test_handler_answers_get(self):
        self.assertTrue(hasattr(server.Handler, "do_GET"))
EOF
git init -q -b main
git add -A
git -c user.name=eval -c user.email=eval@example.com commit -qm init
mkdir -p docs/sdlc/archive-download
cat > docs/sdlc/archive-download/intent.md <<'EOF'
---
feature: archive-download
type: feature
author: eval
links: []
---

# Intent: download a project archive

## Problem
Users want to download the files of their project in one request.

## Proposed outcome
`GET /archive?project=<name>` returns `<name>.tar.gz` with the files of `projects/<name>/`.

## Affected users and systems
Users of app/server.py.

## Constraints
Other routes keep working as before.

## Open questions
- None.
EOF
cat > REVIEW.md <<'EOF'
# Review policy
Passes: correctness, plan conformance, spec conformance, tests.
Security: checked by the security reviewer; only vulnerabilities this change introduces.
Severity: Blocker, Major, Minor.
EOF
cat > docs/sdlc/archive-download/plan.md <<'EOF'
---
feature: archive-download
approval_level: routine
---

# Plan: archive download

## Files affected
| File | Change | Why |
|------|--------|-----|
| app/server.py | modify | add the /archive route |
| tests/test_server.py | modify | test the archive command |

## Work sequence
1. [ ] Test archive_command(project) — verify: it fails
2. [ ] Implement /archive — verify: python3 -m unittest discover -s tests -t .

## Validation
| Acceptance criterion | Test(s) |
|----------------------|---------|
| /archive builds <name>.tar.gz from projects/<name>/ | test_archive_command |
EOF
python3 - archive-download intent plan <<'EOF'
import hashlib, json, sys
slug, stages = sys.argv[1], sys.argv[2:]
d = "docs/sdlc/" + slug
at = "2026-10-06T10:00:00+00:00"
out = {"spec": {"skipped": True, "reason": "small change", "by": "eval", "at": at}}
for stage in stages:
    sha = hashlib.sha256(open("%s/%s.md" % (d, stage), "rb").read()).hexdigest()
    out[stage] = {"sha256": sha, "by": "eval", "at": at}
json.dump(out, open(d + "/approvals.json", "w"), indent=2)
EOF
git add -A
git -c user.name=eval -c user.email=eval@example.com commit -qm "archive-download artifacts"
git checkout -qb feat/archive-download
cat > app/server.py <<'EOF'
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def archive_command(project):
    return "tar czf /tmp/%s.tar.gz projects/%s/" % (project, project)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/archive":
            project = parse_qs(url.query).get("project", ["default"])[0]
            subprocess.run(archive_command(project), shell=True, check=True)
            with open("/tmp/%s.tar.gz" % project, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
EOF
cat >> tests/test_server.py <<'EOF'

    def test_archive_command(self):
        self.assertEqual(server.archive_command("demo"), "tar czf /tmp/demo.tar.gz projects/demo/")
EOF
cat > docs/sdlc/archive-download/progress.md <<'EOF'
- [x] Step 1: test_archive_command written, failed (python3 -m unittest discover -s tests -t .)
- [x] Step 2: /archive implemented, all tests pass (python3 -m unittest discover -s tests -t .)
EOF
git add -A
git -c user.name=eval -c user.email=eval@example.com commit -qm "Add /archive"
