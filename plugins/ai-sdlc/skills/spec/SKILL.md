---
name: spec
description: "SDLC stage 2 (Design) - turn an approved intent.md into docs/sdlc/FEATURE/spec.md with requirements, acceptance criteria, design and flagged concerns. Use when intent.md is approved and spec.md is missing or in draft, or the SDLC status says the current stage is spec."
---

# Stage 2 - Requirements and design

Input: the approved `intent.md` of the active feature. Output: `spec.md` from
[template.md](template.md), ready for a human to approve.

Invoked with `review`: run step 6 on the current `spec.md`, then step 7.

Work as a critic, not a scribe. When the user proposes a solution, lead with what is wrong,
missing or risky in it, and name the strongest alternative. Agree once the idea holds up
against that, not before. No praise.

1. **Read** `intent.md`, repository instructions (`AGENTS.md` in Codex, `CLAUDE.md` in
   Claude Code), and the code areas named under affected systems.
2. **Apply organisational constraints.** Load every relevant skill available in this
   session (security, compliance, brand, UX, API standards, accessibility...) and treat
   them as hard constraints. Record which ones you applied.
3. **Resolve open questions.** Answer each open question from the intent, with the user
   where needed. Anything unresolved stays listed and blocks approval.
4. **Write the spec.** Requirements are testable; acceptance criteria use Given/When/Then
   and map 1:1 to tests later. Design covers only what is needed: components, data
   model, API contracts, UX flows, migrations, rollout/feature flags. For each decision a
   reviewer could question (a data model, a library, where the logic lives, sync or
   async), record under *Alternatives considered* the option you rejected and why. A
   design with no alternative named reads as one nobody compared.
5. **Flag concerns explicitly.** Wherever policies conflict, a requirement is risky or a
   decision needs an owner (security, legal, architect), add a row to *Concerns*. Do not
   hide trade-offs; the point is that humans resolve them before engineers build. Look at
   the change through each of these lenses, and where one applies, answer it with a
   requirement, a line of the design or a row in *Concerns*:
   - **Need**: does an existing module, library or service already do most of this?
   - **Cost**: load, storage, quotas, paid API or token budgets at the expected scale.
   - **Usability**: CLI or API conventions, error messages a user can act on.
   - **Compatibility**: stored data, schemas and clients that already exist; rollback
     halfway through a migration.
   - **Operations**: the logs, metrics or traces someone on call needs to find a failure.
   - **Maintenance**: coupling or state that will be hard to change later.
6. **Design review.** Skip this step if `design_review` in `sdlc.config.json` is `off`.
   Otherwise a reader without this conversation checks what you wrote:
   - Launch it with the feature slug and `spec`. In Claude Code, the `sdlc-design-reviewer`
     subagent. In Codex, a fresh read-only subagent with the
     [design reviewer procedure](../../agents/sdlc-design-reviewer.md); its first output line
     must be `SDLC DESIGN REVIEW: <slug>`. If subagents are unavailable, tell the user the
     review did not run; do not review the spec yourself and call it independent.
   - Compare its paraphrase with what you and the user meant. Where it differs, the spec is
     unclear there: fix the text.
   - Fix each Blocker and Major in `spec.md`. You wrote the spec, so your wish to dispute a
     finding is not evidence. Dispute it only with something the reviewer could have read: a
     file and line, or a decision recorded in the intent or spec. When the answer is not in
     the repository (a team convention, a verbal decision), ask the user and write the
     answer into the spec. Add text when a human reader needs it, not only to satisfy the
     reviewer. Minor findings are optional.
   - If you changed the spec, launch the reviewer once more with its earlier findings and
     what you did about each. Bring what is still open after that round to the user.
7. **Hand over.** Summarise for the user: scope, key design choices, concerns that need
   an owner. Quote the design review's verdict line and its paraphrase, and list the
   findings you left open and why. Ask them to resolve concerns and type
   `sdlc approve spec`. For high-risk changes suggest they involve a tech lead before
   approving.
