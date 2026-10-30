---
feature: <slug>
spec: ./spec.md
approval_level: routine  # routine | elevated
---

# Plan: <title>

## Approach
<!-- The chosen approach in a few sentences, and the main alternative rejected and why. -->

## Files affected
| File | Change | Why |
|------|--------|-----|
|      | create / modify / delete | |

## Work sequence
1. [ ] <step> — verify: <test or command>
2. [ ] <step> — verify: <test or command>
<!-- Each step: which file, what changes in it, and the decisions it needs already made
     (data shape, signature, error handling, dependency). The build and tests pass after it.
     Mark parallelisable steps with (parallel). -->

## Risks
| Risk | Impact | Mitigation |
|------|--------|------------|
|      |        |            |

Rollback: <!-- how to undo safely -->

## Validation
| Acceptance criterion | Test(s) |
|----------------------|---------|
| AC1 | |

Commands: <!-- exact test / lint / typecheck / build commands -->
