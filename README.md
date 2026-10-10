# claude-sdlc

A plugin for Claude Code and Cursor that puts the agent on the rails of Anthropic's
[AI-native SDLC playbook](https://academy.claude.com/courses/ai-native-sdlc-playbook/introduction):

**Intent → Spec → Plan → Build → Verify → Review → Maintain**, with human approval gates
that are enforced by hooks, not just requested in a prompt.

Both hosts share the policy, the artifacts and the approvals, so a person in Claude Code
and a person in Cursor follow one process in one repository. See [Cursor](#cursor) for what
differs there.

## Layers

| Layer | Where | Role |
|-------|-------|------|
| Hooks | `plugins/sdlc/hooks/hooks.json`, `scripts/sdlc.py` | Deterministic gates. The agent cannot skip them |
| Cursor adapter | `plugins/sdlc/hooks/cursor.json`, `scripts/sdlc_cursor.py` | Runs the same gates in Cursor: maps Cursor hook events to the handlers in `sdlc.py` |
| Skills | `plugins/sdlc/skills/*` | Per-stage playbooks and templates, loaded on demand |
| Agent | `plugins/sdlc/agents/sdlc-reviewer.md` | Independent read-only reviewer for stage 5 |
| UI | `plugins/sdlc/hooks/ui.tsx` | A band above the prompt and `SDLC` in the footer: state, next action, approvals by button. Shows state; enforces nothing |
| CLAUDE.md / AGENTS.md section | added by `/sdlc:init` | Short always-on rules for the repository (`AGENTS.md` for Cursor) |
| `sdlc.config.json` | repo root | Opt-in switch plus verify commands. Without it the plugin does nothing |

## Gates

| Gate | Hook | Rule |
|------|------|------|
| Stage status | `UserPromptSubmit` | Every prompt gets a one-line status for the active feature and the next step |
| Approvals | `UserPromptSubmit` | Only a real user message `sdlc approve <stage>` records an approval (sha256 of the artifact, author, time) in `docs/sdlc/<feature>/approvals.json` |
| Stage order | `PreToolUse` Edit/Write | `spec.md` needs an approved intent. `plan.md` needs an approved (or skipped) spec |
| Code gate | `PreToolUse` Edit/Write/Bash | Code changes need an approved intent, spec and plan whose hashes still match. Editing an approved artifact reopens its approval |
| Self-approval | `PreToolUse` | The agent cannot write `approvals.json` or `.sdlc/`, call the hook scripts directly, or send user commands (`sdlc approve ...`) through a nested `claude`, `cursor-agent` or `agent` session. Reading them (`cat`, `grep`, `jq`, `git log`) is allowed |
| Test lock | `PreToolUse` | After `sdlc lock-tests` (bug-fix flow) test files are read-only until the user unlocks them |
| Protected files | `PreToolUse` | Files that steer the agent need user confirmation (`ask`): `CLAUDE.md`, `CLAUDE.local.md` and `AGENTS.md` in any directory, `.claude/` settings, hooks, agents, skills, commands, rules and output styles, `.cursorrules`, `.cursor/` hooks, rules, agents, skills, commands, `mcp.json`, `cli.json`, `environment.json` and `worktrees.json`, `.mcp.json`, `REVIEW.md`, and `sdlc.config.json` in any directory. The Cursor entries and `sdlc.config.json` apply also when a project sets its own `protected_paths` |
| Host configuration | `PreToolUse` | Writes outside the repository to configuration that can add hooks or change plugins need user confirmation: `hooks.json`, `hooks/`, `plugins/`, `cli-config.json`, `mcp.json`, agents and skills under `~/.cursor/`; `settings*.json`, `hooks/`, `plugins/`, agents, skills and commands under `~/.claude/`. A copy into one of these directories, a glob there, a symlinked `~/.claude` and the real file behind a dotfiles link count too. `protected_host_paths` in `sdlc.config.json` adds more. Memory (`~/.claude/projects/`) and other files stay writable |
| One decision per command | `PreToolUse` Bash | A shell command gets its strictest decision: a denied write target wins over one that needs the user and over the deploy gate, so approving a push never lets a gated write through |
| Deploy gate | `PreToolUse` Bash | `gh pr merge`, publish and deploy commands need user confirmation (`ask`). So does a `git push` to a protected branch (`protected_branches`: `main`, `master`, `release/*`), with force, delete, tags or mirror, or one the hook cannot read. A push that only updates other branches passes: the PR and branch protection guard the rest |
| Plan drift | `PreToolUse` Edit/Write | In build, an edit to a code or test file that `plan.md` does not list under *Files affected* gets a note, once per file and session: add the file to the plan and re-approve, or undo the edit. It does not block |
| Definition of Done | `Stop` | If code changed in the session, the `verify` commands run; a failure sends the agent back (max 2 retries per state) |
| Plan mode | `PostToolUse` ExitPlanMode | A plan accepted in plan mode is not the SDLC approval: the agent is told to write it to `plan.md` and ask for `sdlc approve plan` |
| Review and PR | `SubagentStop`, `PostToolUse` | Records the `sdlc-reviewer` verdict with the code it read, and the URL from `gh pr create`, `glab mr create` or a forge MCP tool, for the SDLC bar. Gates nothing |

## User commands (type them as a chat message)

```
sdlc approve intent|spec|plan   approve the current artifact
sdlc skip spec <reason>         small change: go from intent straight to plan
sdlc trivial [reason]           fast-track for this session: no new behaviour (typo, copy, config value)
sdlc trivial off                end fast-track
sdlc unlock tests               allow test edits again
sdlc feature <slug>             switch the active feature
sdlc done                       close the active feature once its PR exists
sdlc status                     show the status (no model turn)
```

## SDLC bar

In an interactive session the plugin shows a band above the prompt, for example
`feat-a  spec.md · waiting for approval  [Approve spec]  ×`. It holds the active feature, its
stage, a warning (`verify failed`, `changes required`, `tests locked`) and the one action that
moves it on:

| When | Action |
|------|--------|
| No active feature | `Start feature` (runs `/sdlc:intent`) and `Fast-track` (`sdlc trivial`) |
| The stage's artifact is missing | `Write intent`, `Write spec`, `Write plan` (runs the stage skill) |
| The artifact is a draft or changed | `Approve <stage>` / `Re-approve <stage>`, one click |
| Build | `Start building` / `Continue building` (runs `/sdlc:build`), with `n/N steps` |
| Every plan step is done, no current review | `Review` (runs `/sdlc:review`); `Review again` after `CHANGES REQUIRED` |
| The review of the current code passed | `Create PR` (runs `/sdlc:review pr`) |
| The PR exists | `Close feature` (`sdlc done`) |
| Claude asked to unlock the tests | `Unlock tests` (`sdlc unlock tests`) |

In the desktop app a new session starts Claude Code with its first message, so the bar
appears after that message. Any user command works as the first message: `sdlc status` shows
the status without a model turn, and `sdlc approve spec` approves as the button does.

The build counts the numbered steps under `## Work sequence` in `plan.md` and the
`- [x] Step N: ...` lines in `progress.md`. A plan without numbered steps counts as done when
verify passes. A review counts while the code it read is unchanged: the `SubagentStop` hook
keeps the `sdlc-reviewer` verdict line (`READY FOR HUMAN REVIEW` or `CHANGES REQUIRED`) with a
hash of the code that differs from the default branch, so a commit on the feature branch keeps
it and any code edit makes it stale. The PR URL comes from the output of `gh pr create`,
`glab mr create` or a forge MCP tool that creates a pull or merge request. Both live in
`.sdlc/features/<feature>.json`. `×` hides the band until the stage, the review or the PR changes.

`SDLC` in the footer, or the feature's name in the band, unfolds the details:

- **Artifacts**: `intent.md`, `spec.md`, `plan.md`, `progress.md` and `approvals.json` with
  their state. A name opens the file in the desktop's Files pane (elsewhere, in the default app).
- **Status**: build steps, the code gate, the last verify result, the review, a link to the PR,
  fast-track and the test lock.
- **Actions**: the next action, `Run verify`, `Show changes` (the Diff pane), `Review`,
  `Close feature`, `Unlock tests`, `End fast-track`, and `Make active` for other features.
- `Init SDLC` (runs `/sdlc:init`) in a repository without `sdlc.config.json`.

A user-only button (`Approve`, `Unlock tests`, `Close feature`, `End fast-track`, `Make active`)
sends the same `sdlc ...` line through the same `UserPromptSubmit` handler a typed command
reaches, so `approvals.json` and `audit.log` look the same. The agent cannot press a button.
`Approve` and `Close feature` wait while Claude's turn runs, because the file may still be half
written. If the artifact changed after it was shown, nothing is approved. A skill button runs
its slash command at once, as if the person typed it. Toasts report an approval that went
stale, the code gate opening or closing, verify passing or failing, the review verdict and the
new PR.

## Plan mode

The playbook drafts the plan in Claude Code plan mode, where Claude reads the code but cannot
change it. The bar does not switch to plan mode: `Write plan` runs `/sdlc:plan` in the current
mode, and Claude writes `plan.md` directly. The code gate already keeps the code untouched until
you approve the plan. Plan mode replaces the session's permission mode while it lasts. In a
`bypassPermissions` session, every Bash command of Claude and its subagents then asks for
permission.

To draft in plan mode anyway, press `Shift+Tab` before you ask for the plan. Claude interviews
you and presents the plan in the template's sections. Accepting it in the plan-mode dialog only
ends plan mode: Claude then writes it to `plan.md`. In both modes the code gate opens when you
approve that file (`Approve plan` or `sdlc approve plan`).

The tests stay locked until the person unlocks them. Claude can only ask: the plugin gives it a
`request_test_unlock` tool (`mcp__sdlc__request_test_unlock`), which turns the band's action into
`Unlock tests`. The tool itself unlocks nothing.

The UI is a Claude Mod: a function-hooks module (`hooks/ui.tsx`). Mods are on by default in
Claude Code 2.1.287 or newer in the terminal, and 2.1.286 or newer in the Code tab of the
Desktop app. Tested with 2.1.291. Older versions skip the module and log that it did not load;
the gates keep working because they are command hooks. In `claude -p` the module runs but
draws nothing, so there is no band.

The agent has a safe CLI on its PATH: `sdlc status | state [--session ID] | features | new <slug> [type] | lock-tests`. `state` prints the status as JSON for UIs.

## Install

Requirements: Claude Code 2.1.287+ (for the UI; the gates work on older versions),
`python3` (3.9+) and `git`.

In an interactive `claude` terminal:

```
/plugin marketplace add Sennjen/claude-sdlc
/plugin install sdlc@ai-sdlc
```

or from a shell:

```bash
claude plugin marketplace add Sennjen/claude-sdlc
claude plugin install sdlc@ai-sdlc
```

Then, inside a project, run `/sdlc:init`. It detects the verify commands, adds the
CLAUDE.md section and `REVIEW.md`, and writes `sdlc.config.json` last. Commit the result.

To try the plugin without installing it, clone the repository and run
`claude --plugin-dir ./claude-sdlc/plugins/sdlc`.

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
  "enabledPlugins": { "sdlc@ai-sdlc": true },
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

## Cursor

The same plugin directory works in Cursor: `plugins/sdlc/.cursor-plugin/plugin.json`
selects `hooks/cursor.json`, and `scripts/sdlc_cursor.py` maps Cursor's hook events to the
handlers in `sdlc.py`. Skills keep their names (`/sdlc:intent`, `/sdlc:plan`, ...), and
`sdlc-reviewer` is a Cursor subagent. There is no Cursor UI: no SDLC bar, buttons or toasts.

Requirements: macOS, Cursor IDE (tested with 3.24.9; the Cursor CLI 2026.10.01 is best
effort), `python3` (3.9+) and `git`.

### Install and update

Cursor loads a local plugin from `~/.cursor/plugins/local/<name>`. A symlink loads only
when it points inside that folder, so clone the repository there and link the plugin:

```bash
git clone https://github.com/Sennjen/claude-sdlc.git ~/.cursor/plugins/local/.src/claude-sdlc
```

```bash
ln -s .src/claude-sdlc/plugins/sdlc ~/.cursor/plugins/local/sdlc
```

Then run **Developer: Reload Window** (or restart Cursor). On Cursor Teams and Enterprise,
the admin setting **Allow Local Plugin Imports** must be on. To update:

```bash
git -C ~/.cursor/plugins/local/.src/claude-sdlc pull
```

and reload the window again.

To enable the SDLC in a repository, run `/sdlc:init` in the agent chat and choose Cursor
(or both hosts). For a repository that already has `sdlc.config.json`, init only adds the
SDLC section to `AGENTS.md`. That file is protected by then, so Cursor blocks the edit and
shows you the section to paste.

Check it works: in an enabled repository, type `sdlc status` in the agent chat. Cursor
shows the status, and the message never reaches the model.

### What differs from Claude Code

| | Claude Code | Cursor |
|-|-------------|--------|
| `[SDLC]` status | Every prompt | Every prompt; at session start only in `agent -p` |
| User commands (`sdlc approve ...`) | Typed message | Typed message; not in `agent -p` (no prompt hook there) |
| Protected files, host configuration | Asks you | Blocked: Cursor ignores `ask` for tool calls, so you make the edit yourself |
| Deploy and push gate | Asks you | Asks you in the IDE; `agent --force` runs the command without asking |
| Definition of Done | Stop hook | Stop hook follow-up; not in `agent -p` |
| Notes about plan drift or a new active feature | Before the edit | Right after the edit |
| Review verdict, PR link, SDLC bar | Yes | No |
| Test unlock request | `request_test_unlock` tool and the bar | You type `sdlc unlock tests` |
| Fast-track (`sdlc trivial`) in subagents | Yes | No: a Cursor subagent has its own session and meets the code gate |
| A gating hook that crashes | The call is denied | The call is denied (`failClosed`) |
| A gating hook that times out | The call runs | The call is denied (`failClosed`) |

### Limits in Cursor

- The edit gate covers Cursor's `Write` (also used for targeted and notebook edits) and
  `Delete` tools, and any other non-read tool whose input names a file or carries a patch
  (`*** Update File: ...`). A write the hook cannot name is judged like a shell write with
  an unknown target. Only the Auto model was tested; another model may use an edit tool
  that Cursor reports differently.
- Cursor's shell keeps its directory between calls but reports it only when the agent sets
  it. A relative write target is judged from the repository root, from the last directory
  the agent set, and from every directory an earlier literal `cd` led to; the strictest
  decision wins. A `cd` through a variable, `pushd` or a script is not tracked.
- The policy root is the workspace root that Cursor names (or an ancestor of it with
  `sdlc.config.json`), never a config next to the edited file. In a multi-root workspace,
  the root that contains `sdlc.config.json` is gated; the others are not. In a workspace
  opened above the repository, an edit is gated by the repository it lands in, but a shell
  call with no reported directory is not checked.
- In Cursor IDE a stop-hook follow-up reaches the prompt hook like a typed message. That is
  why every hook and plugin file of both hosts is protected (see *Host configuration*).
- Cursor imports Claude Code plugins' skills by default. On a machine with both plugins you
  see each `sdlc:*` skill twice; the Claude plugin's hooks are not imported. Accept the
  duplicates or turn off Cursor Settings → Agents → Third-Party Imports.
- Cursor relies on undocumented behaviour (context from the prompt hook, the follow-up path,
  the `${CURSOR_PLUGIN_ROOT}` expansion). After a Cursor update, check that a blocked edit
  is still blocked.

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
- Shell deletes (`rm`) are not gated in either host; Cursor's `Delete` tool is.
- Paths compare case-insensitively, as on macOS (APFS): `.CURSOR/hooks.json` and
  `Approvals.json` are the protected files. On a case-sensitive file system this is
  stricter than needed.
- A glob in a write target outside the repository is judged by its text: if the directory
  it starts in holds host configuration (the home directory or `/` included), the write
  needs the user. The hook never expands it on disk.
- The host configuration check sees edit tools and recognized shell writes. A shell target
  that starts with `~`, `$HOME` or `${HOME}` is judged twice, expanded and as written (the
  shell keeps it as written inside quotes), and the stricter decision wins. A script that
  writes there is not seen.
- On Claude Code 2.1.291 a hook's `ask` holds in `bypassPermissions` mode too:
  `claude -p --dangerously-skip-permissions` denies the gated call, so an interactive session
  should show the prompt. Older versions may not; set `"gated_command_decision": "deny"` if
  deploys must be done by hand.

## Tests

```bash
cd plugins/sdlc && python3 -m unittest discover -s tests -v
```

`tests/test_cursor_hooks.py` runs the Cursor adapter on sanitized payloads that Cursor IDE
and CLI sent (`tests/fixtures/cursor/payloads.json`).

The UI module has its own tests and type check (Claude Code 2.1.284+, TypeScript 5.4+):

```bash
claude plugin validate plugins/sdlc && claude plugin test plugins/sdlc
```

```bash
cd plugins/sdlc && npx -p typescript tsc -p .
```

The eval suite in `plugins/sdlc/evals/` checks the agent's behaviour, not the hooks: a feature
request starts with `intent.md`, an approved intent gets a spec that flags conflicting
constraints, a skipped spec gets a plan before code, a bug fix locks the tests before the fix,
work outside the approved plan stays out of the change, a typo asks for `sdlc trivial`, a
question starts nothing, the review runs the `sdlc-reviewer` subagent, and a push to `main`
waits for the user. Each case runs three times with the plugin and three times without it, so
`Δ` shows what the plugin adds. It makes real model calls (roughly 54 agent runs), so run it
from a terminal where `claude` is logged in:

```bash
cd plugins/sdlc && claude plugin eval . --scaffold --allow-tools Bash Write Edit
```

```bash
cd plugins/sdlc && claude plugin eval . --case trivial-change-asks-for-fast-track --runs 1 --ablation none --scaffold --allow-tools Bash Write Edit
```

The second command runs one case once, for iterating on a skill. `--scaffold` runs each case's
`fixture.sh`, which builds a small repository with the SDLC enabled.

`tsc` reads the engine's declarations from `.claude-plugin/types/` (gitignored). Claude Code
writes them there when it loads the plugin from this folder (`claude --plugin-dir plugins/sdlc`).

## License

[MIT](LICENSE)
