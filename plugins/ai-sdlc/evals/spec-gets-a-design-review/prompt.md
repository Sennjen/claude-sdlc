---
description: Before the user approves a spec, the sdlc-design-reviewer subagent reads it with fresh context, the hook records its verdict, and the handover reports it.
tags: [spec, design-review]
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit, Agent]
---

The intent for shout-option is approved. Go ahead.
