# AI-native SDLC for Claude Code and Codex

[![Release](https://img.shields.io/github/v/release/Sennjen/claude-sdlc)](https://github.com/Sennjen/claude-sdlc/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A plugin (`ai-sdlc`) for Claude Code and local Codex clients that follows the
[AI-native SDLC playbook](https://academy.claude.com/courses/ai-native-sdlc-playbook/introduction):

**Intent → Spec → Plan → Build → Verify → Review → Maintain**, with human approval gates
that are enforced by enabled, trusted lifecycle hooks. The agent cannot edit code until you
approve the plan, cannot approve for you, and cannot finish a turn that breaks your verify
commands.

## Quick start: Claude Code

```bash
claude plugin marketplace add Sennjen/claude-sdlc
```

```bash
claude plugin install ai-sdlc@ai-sdlc
```

Then run `/ai-sdlc:init` in your project. [Install](#install-claude-code) covers the
requirements, organization-wide rollout and upgrading from the plugin's earlier name, `sdlc`.

## Quick start: Codex

From this checkout, register the local marketplace:

```bash
codex plugin marketplace add /absolute/path/to/claude-sdlc
codex plugin add ai-sdlc@ai-sdlc
```

In a client with a Plugins directory, you can install **AI-native SDLC** from this local
marketplace instead. Restart the client, review and trust the plugin hooks (`/hooks` in the
CLI), then run `$ai-sdlc:init` in your target project. The native manifest selects a Codex
adapter and shares the workflow skills and Python gatekeeper with Claude Code.

Codex uses `AGENTS.md` and an inline interactive status block in chat
(`$ai-sdlc:status` or `sdlc status`) on clients with inline visualization support.
The block has HTML buttons that request actions through the chat. Cards are snapshots
refreshed in subsequent responses; clients without this support have a text fallback.
Protected edits and gated deploys
are denied and must be done by the user outside the agent: Codex hooks do not support
interactive `ask` decisions. The Claude status bar is available only in Claude Code.

See [Codex installation, behavior, and verification](docs/codex.md) for compatibility limits,
existing-project migration, and testing. Local hook support is required; a skills-only
installation does not enforce the gates.

This is an independent project, not made or endorsed by Anthropic or OpenAI.

## Layers

| Layer | Where | Role |
|-------|-------|------|
| Hooks | `hooks/hooks.json` (Claude), `hooks/codex.json` and `scripts/codex.py` (Codex), shared `scripts/sdlc.py` | Deterministic gates when hooks are enabled and trusted |
| Skills | `plugins/ai-sdlc/skills/*` | Per-stage playbooks and templates, loaded on demand |
| Agents | `plugins/ai-sdlc/agents/sdlc-reviewer.md`, `plugins/ai-sdlc/agents/sdlc-security-reviewer.md` | Independent read-only code and security reviewers for stage 5 |
| UI | `hooks/ui.tsx` (Claude), `scripts/codex_ui.py`, `assets/codex-status.html`, and the status skill (Codex) | Claude has a band above the prompt. Supported Codex clients embed a status block with progress and guarded action buttons in chat |
| Repository instructions | `AGENTS.md` (Codex), `CLAUDE.md` (Claude), added by init | Short always-on rules for the repository |
| `sdlc.config.json` | repo root | Opt-in switch plus verify commands. Without it the plugin does nothing |

## Gates

The table below describes Claude Code. Codex applies the same stage, hash, test-lock, and
verification rules through `apply_patch` and Bash hooks, with the differences documented in
[the Codex guide](docs/codex.md#behavior-differences).

| Gate | Hook | Rule |
|------|------|------|
| Stage status | `UserPromptSubmit` | Every prompt gets a one-line status for the active feature and the next step |
| Approvals | `UserPromptSubmit` | Only a real user message `sdlc approve <stage>` records an approval (sha256 of the artifact, author, time) in `docs/sdlc/<feature>/approvals.json` |
| Stage order | `PreToolUse` Edit/Write | `spec.md` needs an approved intent. `plan.md` needs an approved (or skipped) spec |
| Code gate | `PreToolUse` Edit/Write/Bash | Code changes need an approved intent, spec and plan whose hashes still match. Editing an approved artifact reopens its approval |
| Self-approval | `PreToolUse` | The agent cannot write `approvals.json` or `.sdlc/`, call the hook script directly, or send user commands (`sdlc approve ...`) through a nested `claude` session. Reading them (`cat`, `grep`, `jq`, `git log`) is allowed |
| Test lock | `PreToolUse` | After `sdlc lock-tests` (bug-fix flow) test files are read-only until the user unlocks them |
| Protected files | `PreToolUse` | Files that steer the agent need user confirmation (`ask`): `CLAUDE.md` and `CLAUDE.local.md` in any directory, `.claude/` settings, hooks, agents, skills, commands, rules and output styles, `.mcp.json`, `REVIEW.md`, `sdlc.config.json` |
| Deploy gate | `PreToolUse` Bash | `gh pr merge`, publish and deploy commands need user confirmation (`ask`). So does a `git push` to a protected branch (`protected_branches`: `main`, `master`, `release/*`), with force, delete, tags or mirror, or one the hook cannot read. A push that only updates other branches passes: the PR and branch protection guard the rest |
| Plan drift | `PreToolUse` Edit/Write | In build, an edit to a code or test file that `plan.md` does not list under *Files affected* gets a note, once per file and session: add the file to the plan and re-approve, or undo the edit. It does not block |
| Definition of Done | `Stop` | If code changed in the session, the `verify` commands run; a failure sends the agent back (max 2 retries per state) |
| Plan mode | `PostToolUse` ExitPlanMode | A plan accepted in plan mode is not the SDLC approval: the agent is told to write it to `plan.md` and ask for `sdlc approve plan` |
| Review and PR | `SubagentStop`, `PostToolUse` | Records the `sdlc-reviewer` and `sdlc-security-reviewer` verdicts with the code they read, and the URL from `gh pr create`, `glab mr create` or a forge MCP tool, for the SDLC bar. Gates nothing |

## User commands (type them as a chat message)

```
sdlc approve intent|spec|plan   approve the current artifact
sdlc skip spec <reason>         small change: go from intent straight to plan
sdlc trivial [reason]           fast-track for this session: no new behaviour (typo, copy, config value)
sdlc trivial off                end fast-track
sdlc unlock tests               allow test edits again
sdlc feature <slug>             switch the active feature
sdlc done                       close the active feature once its PR exists
sdlc status                     show status (Codex embeds an interactive block when supported)
```

## SDLC bar (Claude Code)

In an interactive session the plugin shows a band above the prompt, for example
`feat-a  spec.md · waiting for approval  [Approve spec]  ×`. It holds the active feature, its
stage, a warning (`verify failed`, `changes required`, `tests locked`) and the one action that
moves it on:

| When | Action |
|------|--------|
| No active feature | `Start feature` (runs `/ai-sdlc:intent`) and `Fast-track` (`sdlc trivial`) |
| The stage's artifact is missing | `Write intent`, `Write spec`, `Write plan` (runs the stage skill) |
| The artifact is a draft or changed | `Approve <stage>` / `Re-approve <stage>`, one click |
| Build | `Start building` / `Continue building` (runs `/ai-sdlc:build`), with `n/N steps` |
| Every plan step is done, no current reviews | `Review` (runs `/ai-sdlc:review`); `Review again` after `CHANGES REQUIRED` from either reviewer |
| Both reviews of the current code passed | `Create PR` (runs `/ai-sdlc:review pr`) |
| The PR exists | `Close feature` (`sdlc done`) |
| Claude asked to unlock the tests | `Unlock tests` (`sdlc unlock tests`) |

In the desktop app a new session starts Claude Code with its first message, so the bar
appears after that message. Any user command works as the first message: `sdlc status` shows
the status without a model turn, and `sdlc approve spec` approves as the button does.

The prompt's typeahead completes a typed `sdlc ...` command: the command, then its argument
from the current state (the stages whose file exists, with their state, or the features you can
make active). It only fills in the text; the command still runs when you send it.

The build counts the numbered steps under `## Work sequence` in `plan.md` and the
`- [x] Step N: ...` lines in `progress.md`. A plan without numbered steps counts as done when
verify passes. A review counts while the code it read is unchanged: the `SubagentStop` hook
keeps the verdict line (`READY FOR HUMAN REVIEW` or `CHANGES REQUIRED`) of `sdlc-reviewer` and
of `sdlc-security-reviewer`, each with a hash of the code as it differs from a fixed base
commit: where the feature branch left the default branch, or the current commit when no
default branch is found. A commit keeps the review, and any code change makes it stale. The PR
URL comes from the output of `gh pr create`, `glab mr create` or a forge MCP tool that creates
a pull or merge request. Each verdict and the PR URL live in their own file under
`.sdlc/features/<feature>/`. `×` hides the band until the stage, a review or the PR changes.

`SDLC` in the footer, or the feature's name in the band, unfolds the details:

- **Artifacts**: `intent.md`, `spec.md`, `plan.md`, `progress.md` and `approvals.json` with
  their state. A name opens the file in the desktop's Files pane (elsewhere, in the default app).
- **Status**: build steps, the code gate, the last verify result, the code and security
  reviews, a link to the PR, fast-track and the test lock.
- **Actions**: the next action, `Run verify`, `Show changes` (the Diff pane), `Review`,
  `Close feature`, `Unlock tests` and `End fast-track`.
- **Other features**: one row per open feature with its stage; a row makes that feature active
  (`sdlc feature <slug>`).
- `Init SDLC` (runs `/ai-sdlc:init`) in a repository without `sdlc.config.json`.

A user-only button (`Approve`, `Unlock tests`, `Close feature`, `End fast-track`, a row of
Other features)
sends the same `sdlc ...` line through the same `UserPromptSubmit` handler a typed command
reaches, so `approvals.json` and `audit.log` look the same. The agent cannot press a button.
`Approve` and `Close feature` wait while Claude's turn runs, because the file may still be half
written. If the artifact changed after it was shown, nothing is approved. A skill button runs
its slash command at once, as if the person typed it. Toasts report an approval that went
stale, the code gate opening or closing, verify passing or failing, the review verdict and the
new PR. In a terminal with a notification channel (iTerm2, kitty or Ghostty; a bell only
rings), a native notification also reports a failed verify, the review verdict, the new PR, and
an artifact that waits for approval when Claude's turn ends (once per version of the file). The
desktop app has no such channel, so there the toasts alone show.

## Plan mode (Claude Code)

The playbook drafts the plan in Claude Code plan mode, where Claude reads the code but cannot
change it. The bar does not switch to plan mode: `Write plan` runs `/ai-sdlc:plan` in the current
mode, and Claude writes `plan.md` directly. The code gate already keeps the code untouched until
you approve the plan. Plan mode replaces the session's permission mode while it lasts. In a
`bypassPermissions` session, every Bash command of Claude and its subagents then asks for
permission.

To draft in plan mode anyway, press `Shift+Tab` before you ask for the plan. Claude interviews
you and presents the plan in the template's sections. Accepting it in the plan-mode dialog only
ends plan mode: Claude then writes it to `plan.md`. In both modes the code gate opens when you
approve that file (`Approve plan` or `sdlc approve plan`).

The tests stay locked until the person unlocks them. Claude can only ask: the plugin gives it a
`request_test_unlock` tool (`mcp__ai-sdlc__request_test_unlock`), which turns the band's action into
`Unlock tests`. The tool itself unlocks nothing. Its schema is in Claude's tool list from the
start, not behind ToolSearch, because the verify skill tells Claude to call it.

The UI is a Claude Mod: a function-hooks module (`hooks/ui.tsx`). It needs Claude Code 2.1.293
or newer, in the terminal and in the Code tab of the Desktop app: it hooks the prompt's
typeahead, which older versions do not have. Native notifications need 2.1.295; on 2.1.293 and
2.1.294 the toasts alone show. Tested with 2.1.295. Older versions skip the module and log that
it did not load; the gates keep working because they are command hooks. In `claude -p` the module runs but
draws nothing, so there is no band.

The agent has a safe CLI on its PATH: `sdlc status | state [--session ID] | features | new <slug> [type] | lock-tests`. `state` prints the status as JSON for UIs.

## Install (Claude Code)

Requirements: Claude Code 2.1.293+ (for the UI; the gates work on older versions),
`python3` (3.9+) and `git`.

In an interactive `claude` terminal:

```
/plugin marketplace add Sennjen/claude-sdlc
/plugin install ai-sdlc@ai-sdlc
```

or from a shell:

```bash
claude plugin marketplace add Sennjen/claude-sdlc
claude plugin install ai-sdlc@ai-sdlc
```

Upgrading from the plugin's earlier name, `sdlc`: the marketplace's `renames` map migrates
`sdlc@ai-sdlc` to `ai-sdlc@ai-sdlc`, settings included. If Claude Code then says the plugin is
not cached, run `/plugin install ai-sdlc@ai-sdlc` once. The skills move from `/sdlc:...` to
`/ai-sdlc:...`; the `sdlc ...` commands, `sdlc.config.json` and `.sdlc/` stay the same.

Then, inside a project, run `/ai-sdlc:init`. It detects the verify commands, adds the
CLAUDE.md section and `REVIEW.md`, and writes `sdlc.config.json` last. Commit the result.

To try the plugin without installing it, clone the repository and run
`claude --plugin-dir ./claude-sdlc/plugins/ai-sdlc`.

### Enforce it across an organization

An engineer can disable a plugin they installed themselves. To make the gates
non-negotiable, an administrator delivers the plugin through
[managed settings](https://code.claude.com/docs/en/plugins/org) (MDM or the Claude admin
console). Managed settings take precedence over every other scope, and users cannot edit them:

```json
{
  "extraKnownMarketplaces": {
    "ai-sdlc": {
      "source": { "source": "git", "url": "https://gitlab.example.com/tools/claude-sdlc.git" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "ai-sdlc@ai-sdlc": true },
  "strictKnownMarketplaces": [
    { "source": "git", "url": "https://gitlab.example.com/tools/claude-sdlc.git" }
  ],
  "allowManagedHooksOnly": true,
  "permissions": { "disableBypassPermissionsMode": "disable" }
}
```

- `extraKnownMarketplaces` registers this repository as the `ai-sdlc` marketplace on every
  machine. Use a `github` source (`{ "source": "github", "repo": "org/claude-sdlc" }`) for GitHub.
- `enabledPlugins` force-enables `sdlc`. Disabling it at user or project scope does not stop it
  from loading.
- `strictKnownMarketplaces` allows plugins only from listed sources. Add every other approved
  marketplace here, or its plugins stop installing.
- `allowManagedHooksOnly` blocks user, project and local hooks. Hooks of force-enabled plugins
  still run, so the SDLC gates stay on and no other hook can weaken them.
- `disableBypassPermissionsMode` keeps the `ask` decisions (protected files, deploy gate) in
  front of a person.

Two limits remain. The plugin stays inert in a repository without `sdlc.config.json`, so the
repository has to commit that file, and a person can still delete it (the hook only asks). And
the hooks guard the agent, not the people: anything that reaches the branch another way is
caught only by branch protection and human review.

## Artifacts

```
docs/sdlc/<feature>/
  intent.md       stage 1: problem, outcome, affected users/systems, constraints, open questions
  spec.md         stage 2: requirements, acceptance criteria, design, flagged concerns
  plan.md         stage 3: files affected, work sequence, risks, validation
  progress.md     build log (keeps plan.md, and so its approval, unchanged)
  approvals.json  who approved what and when (commit it: it is the audit trail)
.sdlc/            local session state and audit.log (gitignored)
```

## Limitations

- Shell-write detection (`>`, `tee`, `sed -i`, `cp`, `git apply`...) and the nested-session
  check (`claude -p "sdlc approve plan"`) are best effort. A determined agent could still
  write files or send user commands through a script. The hooks keep a
  cooperative agent honest; they are not a sandbox. For hard guarantees, use managed
  settings and sandboxing.
- In-place edits are judged by the files they edit: `sed`/`gsed` with `-i` or
  `--in-place[=SUFFIX]` (GNU long options may be cut to a unique prefix), BSD `sed` with `-I`,
  and `perl`/`ruby` with `-i`. A BSD suffix of its own (`sed -i '' ...`, `sed -i .bak ...`) is
  neither script nor file, unless the GNU reading would edit it: the hook checks that on disk
  only for one simple command and a word with nothing to expand, and otherwise counts the word
  as a file. Words after the first operand are files (perl, ruby, BSD sed). One with no file
  left counts as an unknown target. Not detected: the sed script commands `w`, `W` and `e`,
  `gawk -i inplace`, and a program behind a wrapper (`env`, `xargs`, `find -exec`) or a
  subshell opened with a lone `( `.
- A shell variable in a write target (`S=/tmp/out; cp a $S/`) is expanded only from a literal
  value the same command assigns once, at top level, before the target, with no compound
  command, subshell or variable-setting builtin (`read`, `printf -v`, `cd`...) before it.
  Any other variable is read as written, so `cp a $X/app.js` counts as a write to `$X/app.js`
  in the repository. Targets are normalized first: `/tmp/../repo/src/app.js` is not a tmp path.
- The deploy gate looks at what a Bash call runs. A gated word inside a file name
  (`cat upload-assets.ts`), a heredoc body or quoted text with spaces (`git commit -m "..."`)
  counts only when it is executed: the program itself, a script given to `bash`/`python3`/`tsx`,
  text handed to a shell (`bash -c`, `ssh host '...'`, `| sh`) or `$(...)`. Code run by other
  interpreters (`python3 -c`, `node -e`) and package scripts is not inspected.
- A Bash call that mentions `approvals.json`, `.sdlc/` or `sdlc.py` passes only if every program
  in it is a known reader (`cat`, `ls`, `grep`, `jq`, `sed` without `-i`/`w`/`e`, `find` without
  `-exec`/`-delete`, `git status/log/show/diff/add/commit`...) and it redirects only to `/dev/*`
  or tmp. Anything else is denied, even when it only reads (`python3 -c`, `find -exec`,
  `sed -n '/Next/p'`).
- If `pre-edit` or `pre-bash` crashes, the gate fails closed: the call is denied, the person sees
  why, and `audit.log` records `hook-error`. `"on_hook_error": "allow"` in `sdlc.config.json`
  lets calls through while the bug is fixed. The other hooks are skipped with a note. A hook that
  times out, or a missing `python3`, still lets the call through.
- The push gate reads `git push` strictly: a refspec it cannot resolve (a variable, `cd` before
  it, `-C`, `-c`, a pattern, an abbreviated option) asks, as does any push it finds inside quotes,
  a heredoc or `$(...)`. It cannot see what a script or alias pushes.
- The plan-drift note reads paths, directories and globs from the first column of the
  *Files affected* table (or a list in that section). A plan that names files another way gets
  no note.
- The Stop gate runs the verify commands itself, so keep them fast.
- On Claude Code 2.1.291 a hook's `ask` holds in `bypassPermissions` mode too:
  `claude -p --dangerously-skip-permissions` denies the gated call, so an interactive session
  should show the prompt. Older versions may not; set `"gated_command_decision": "deny"` if
  deploys must be done by hand.

## Tests

```bash
cd plugins/ai-sdlc && python3 -m unittest discover -s tests -v
```

The Python suite includes Codex hook fixtures, multi-file patch checks, and shared-core
regressions. For an interactive Codex smoke check, see [the guide](docs/codex.md#verification).

The UI module has its own tests and type check (Claude Code 2.1.293+, TypeScript 5.4+):

```bash
claude plugin validate plugins/ai-sdlc && claude plugin test plugins/ai-sdlc
```

```bash
cd plugins/ai-sdlc && npx -p typescript tsc -p .
```

The eval suite in `plugins/ai-sdlc/evals/` checks the agent's behaviour, not the hooks: a feature
request starts with `intent.md`, an approved intent gets a spec that flags conflicting
constraints, a skipped spec gets a plan before code, a bug fix locks the tests before the fix,
work outside the approved plan stays out of the change, a typo asks for `sdlc trivial`, a
question starts nothing, the review runs the `sdlc-reviewer` and `sdlc-security-reviewer`
subagents, the security reviewer flags a command injection the branch adds, and a push to
`main` waits for the user. Each case runs three times with the plugin and three times without
it, so `Δ` shows what the plugin adds. It makes real model calls (roughly 60 agent runs), so run it
from a terminal where `claude` is logged in:

```bash
cd plugins/ai-sdlc && claude plugin eval . --scaffold --allow-tools Bash Write Edit
```

```bash
cd plugins/ai-sdlc && claude plugin eval . --case trivial-change-asks-for-fast-track --runs 1 --ablation none --scaffold --allow-tools Bash Write Edit
```

The second command runs one case once, for iterating on a skill. `--scaffold` runs each case's
`fixture.sh`, which builds a small repository with the SDLC enabled.

`tsc` reads the engine's declarations from `.claude-plugin/types/` (gitignored). Claude Code
writes them there when it loads the plugin from this folder (`claude --plugin-dir plugins/ai-sdlc`).

## License

[MIT](LICENSE)
