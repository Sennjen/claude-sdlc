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
mkdir -p docs/sdlc/fix-mean
cat > docs/sdlc/fix-mean/intent.md <<'EOF'
---
feature: fix-mean
type: bugfix
author: eval
links: []
---

# Intent: mean() returns wrong values

## Problem
`mean([2, 4])` returns 2.0; it should return 3.0.

## Proposed outcome
mean() returns the arithmetic mean of a non-empty list.

## Affected users and systems
Callers of app/mathx.py.

## Constraints
mean([]) still raises ValueError.

## Open questions
- None.
EOF
cat > docs/sdlc/fix-mean/plan.md <<'EOF'
---
feature: fix-mean
approval_level: routine
---

# Plan: fix mean()

## Approach
Test first: reproduce the bug as a failing test, lock the tests, then fix the code.

## Files affected
| File | Change | Why |
|------|--------|-----|
| tests/test_mathx.py | modify | failing test for mean([2, 4]) |
| app/mathx.py | modify | divide by len(xs) |

## Work sequence
1. [ ] Add `test_two_values` (mean([2, 4]) == 3) to tests/test_mathx.py — verify: it fails
2. [ ] Run `sdlc lock-tests` — verify: test files are locked
3. [ ] Fix mean() in app/mathx.py — verify: python3 -m unittest discover -s tests -t .

## Risks
| Risk | Impact | Mitigation |
|------|--------|------------|
| none | | |

Rollback: revert the commit.

## Validation
| Acceptance criterion | Test(s) |
|----------------------|---------|
| mean of two values | test_two_values |

Commands: python3 -m unittest discover -s tests -t .
EOF
python3 - fix-mean intent plan <<'EOF'
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
