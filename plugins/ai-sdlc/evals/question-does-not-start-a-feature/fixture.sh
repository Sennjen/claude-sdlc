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
