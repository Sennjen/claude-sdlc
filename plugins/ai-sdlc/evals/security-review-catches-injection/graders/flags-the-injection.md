---
type: llm
---

PASS only if the reply reports, as a finding of the review, that the `project` query parameter
reaches a shell command, so a caller can run their own commands on the server (command or shell
injection). Any wording counts, and so does a report that the finding was fixed.
FAIL if the reply does not mention this problem, mentions only other findings such as path
traversal, or says the review found no security issue.
