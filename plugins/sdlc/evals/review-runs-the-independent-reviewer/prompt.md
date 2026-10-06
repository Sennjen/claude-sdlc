---
description: The review stage hands the diff to the sdlc-reviewer subagent with fresh context.
tags: [review]
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Agent]
---

The shout-option build is finished and verified. Review this branch against main before we open a PR. Do not open the PR yet.
