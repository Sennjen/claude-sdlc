---
type: llm
focus: { source: file, path: docs/sdlc/shout-option/spec.md }
---

PASS if the spec names the conflict between the CLI policy (every flag must be listed in
README.md) and the constraint that this change must not edit README.md, and records it as
an open concern with an owner or a decision for a person to make.
FAIL if the spec does not mention the conflict, quietly drops one of the two constraints,
or plans to edit README.md without flagging it.
