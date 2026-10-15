---
description: A one-word fix with no active feature asks the user for `sdlc trivial` instead of editing past the gate.
tags: [smoke, gate]
max_turns: 15
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Fix the typo in app/greet.py: the greeting says Helo instead of Hello.
