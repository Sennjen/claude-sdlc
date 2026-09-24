---
name: spec
description: SDLC stage 2 (Design) - turn an approved intent.md into docs/sdlc/<feature>/spec.md with requirements, acceptance criteria, design and flagged concerns. Use when intent.md is approved and spec.md is missing or in draft, or the SDLC status says the current stage is spec.
---

# Stage 2 - Requirements and design

Input: the approved `intent.md` of the active feature. Output: `spec.md` from
[template.md](template.md), ready for a human to approve.

1. **Read** `intent.md`, `CLAUDE.md`, and the code areas named under affected systems.
2. **Apply organisational constraints.** Load every relevant skill available in this
   session (security, compliance, brand, UX, API standards, accessibility...) and treat
   them as hard constraints. Record which ones you applied.
3. **Resolve open questions.** Answer each open question from the intent, with the user
   where needed. Anything unresolved stays listed and blocks approval.
4. **Write the spec.** Requirements are testable; acceptance criteria use Given/When/Then
   and map 1:1 to tests later. Design covers only what is needed: components, data
   model, API contracts, UX flows, migrations, rollout/feature flags.
5. **Flag concerns explicitly.** Wherever policies conflict, a requirement is risky or a
   decision needs an owner (security, legal, architect), add a row to *Concerns*. Do not
   hide trade-offs; the point is that humans resolve them before engineers build.
6. **Hand over.** Summarise for the user: scope, key design choices, concerns that need
   an owner. Ask them to resolve concerns and type `sdlc approve spec`. For high-risk
   changes suggest they involve a tech lead before approving.
