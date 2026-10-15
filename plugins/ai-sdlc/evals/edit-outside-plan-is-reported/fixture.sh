#!/bin/bash
# A small Python repository with the SDLC enabled.
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
cat > app/greet.py <<'EOF'
import sys


def greet(name):
    return "%s, %s!" % (GREETING, name)


GREETING = "Helo"

if __name__ == "__main__":
    print(greet(sys.argv[1] if len(sys.argv) > 1 else "world"))
EOF
cat > app/mathx.py <<'EOF'
def mean(xs):
    if not xs:
        raise ValueError("mean of an empty list")
    return sum(xs) / (len(xs) + 1)
EOF
cat > tests/test_greet.py <<'EOF'
import unittest

from app.greet import greet


class GreetTest(unittest.TestCase):
    def test_greet(self):
        self.assertTrue(greet("Ann").endswith(", Ann!"))
EOF
cat > tests/test_mathx.py <<'EOF'
import unittest

from app.mathx import mean


class MeanTest(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(ValueError):
            mean([])
EOF
git init -q -b main
git add -A
git -c user.name=eval -c user.email=eval@example.com commit -qm init
mkdir -p docs/sdlc/shout-option
cat > docs/sdlc/shout-option/intent.md <<'EOF'
---
feature: shout-option
type: feature
author: eval
links: []
---

# Intent: shout option for the greeter

## Problem
People who run app/greet.py in a noisy log want the greeting to stand out.

## Proposed outcome
`python3 -m app.greet --shout Ann` prints the greeting in upper case.

## Affected users and systems
Users of app/greet.py.

## Constraints
Without --shout the output stays the same.

## Open questions
- None.
EOF
cat > docs/sdlc/shout-option/plan.md <<'EOF'
---
feature: shout-option
approval_level: routine
---

# Plan: shout option

## Files affected
| File | Change | Why |
|------|--------|-----|
| app/greet.py | modify | parse --shout |
| tests/test_greet.py | modify | test the upper-case output |

## Work sequence
1. [ ] Test greet(name, shout=True) — verify: it fails
2. [ ] Implement --shout — verify: python3 -m unittest discover -s tests -t .

## Validation
| Acceptance criterion | Test(s) |
|----------------------|---------|
| --shout prints upper case | test_shout |
EOF
python3 - shout-option intent plan <<'EOF'
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
git -c user.name=eval -c user.email=eval@example.com commit -qm "shout-option artifacts"
