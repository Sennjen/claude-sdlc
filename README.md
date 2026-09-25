# claude-sdlc

A Claude Code plugin that puts the agent on the rails of Anthropic's
[AI-native SDLC playbook](https://academy.claude.com/courses/ai-native-sdlc-playbook/introduction):

**Intent → Spec → Plan → Build → Verify → Review → Maintain**, with human approval gates
that are enforced by hooks, not just requested in a prompt.

## Layers

| Layer | Where | Role |
|-------|-------|------|
| Hooks | `plugins/sdlc/hooks/hooks.json`, `scripts/sdlc.py` | Deterministic gates. The agent cannot skip them |
| Skills | `plugins/sdlc/skills/*` | Per-stage playbooks and templates, loaded on demand |
| Agent | `plugins/sdlc/agents/sdlc-reviewer.md` | Independent read-only reviewer for stage 5 |
| CLAUDE.md section | added by `/sdlc:init` | Short always-on rules for the repository |
| `sdlc.config.json` | repo root | Opt-in switch plus verify commands. Without it the plugin does nothing |

## Gates

| Gate | Hook | Rule |
|------|------|------|
| Stage status | `UserPromptSubmit` | Every prompt gets a one-line status for the active feature and the next step |
| Approvals | `UserPromptSubmit` | Only a real user message `sdlc approve <stage>` records an approval (sha256 of the artifact, author, time) in `docs/sdlc/<feature>/approvals.json` |
| Stage order | `PreToolUse` Edit/Write | `spec.md` needs an approved intent. `plan.md` needs an approved (or skipped) spec |
| Code gate | `PreToolUse` Edit/Write/Bash | Code changes need an approved intent, spec and plan whose hashes still match. Editing an approved artifact reopens its approval |
| Self-approval | `PreToolUse` | The agent cannot write `approvals.json` or `.sdlc/`, call the hook script directly, or send user commands (`sdlc approve ...`) through a nested `claude` session. Reading them (`cat`, `grep`, `jq`, `git log`) is allowed |
| Test lock | `PreToolUse` | After `sdlc lock-tests` (bug-fix flow) test files are read-only until the user unlocks them |
| Protected files | `PreToolUse` | Files that steer the agent need user confirmation (`ask`): `CLAUDE.md` and `CLAUDE.local.md` in any directory, `.claude/` settings, hooks, agents, skills, commands, rules and output styles, `.mcp.json`, `REVIEW.md`, `sdlc.config.json` |
| Deploy gate | `PreToolUse` Bash | `git push`, `gh pr merge`, publish and deploy commands need user confirmation (`ask`) |
| Definition of Done | `Stop` | If code changed in the session, the `verify` commands run; a failure sends the agent back (max 2 retries per state) |

## User commands (type them as a chat message)

```
sdlc approve intent|spec|plan   approve the current artifact
sdlc skip spec <reason>         small change: go from intent straight to plan
sdlc trivial [reason]           fast-track for this session (typo, config, one-liner)
sdlc trivial off                end fast-track
sdlc unlock tests               allow test edits again
sdlc feature <slug>             switch the active feature
sdlc done                       close the active feature after merge
sdlc status                     show the status
```

The agent has a safe CLI on its PATH: `sdlc status | features | new <slug> [type] | lock-tests`.

## Install

Requirements: `python3` (3.9+) and `git`.

In an interactive `claude` terminal:

```
/plugin marketplace add /path/to/claude-sdlc
/plugin install sdlc@ai-sdlc
```

or from a shell:

```bash
claude plugin marketplace add /path/to/claude-sdlc
claude plugin install sdlc@ai-sdlc
```

Then, inside a project, run `/sdlc:init`. It detects the verify commands, adds the
CLAUDE.md section and `REVIEW.md`, and writes `sdlc.config.json` last. Commit the result.

To try the plugin without installing it: `claude --plugin-dir /path/to/claude-sdlc/plugins/sdlc`.

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
- Hooks fail open: if the hook script crashes, the tool call proceeds and the error goes to stderr.
- The Stop gate runs the verify commands itself, so keep them fast.
- Whether `ask` decisions still prompt in `bypassPermissions` mode depends on the Claude Code
  version. Set `"gated_command_decision": "deny"` if deploys must be done by hand.

## Tests

```bash
cd plugins/sdlc && python3 -m unittest discover -s tests -v
```
