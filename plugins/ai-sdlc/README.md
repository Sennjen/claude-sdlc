# AI-native SDLC for Claude Code

Claude Code writes code fast, and skips the parts of the job that keep a team safe: agreeing on
what to build, writing it down, and waiting for a human to say yes. This plugin puts those steps
back, and enforces them with hooks instead of asking nicely in a prompt.

Every change moves through **Intent → Spec → Plan → Build → Verify → Review**. Claude writes each
artifact under `docs/sdlc/<feature>/`, and you approve it. Until the plan is approved, Claude
cannot edit code. When it is done, the Definition of Done runs your verify commands, and an
independent reviewer agent checks the change before the PR.

The workflow follows the [AI-native SDLC playbook](https://academy.claude.com/courses/ai-native-sdlc-playbook/introduction)
from Anthropic Academy. This plugin is an independent project, not made or endorsed by Anthropic.

## What you get

- **Approval gates.** Only a message you type, `sdlc approve intent|spec|plan`, records an
  approval, with the artifact's sha256, your name and the time. Editing an approved artifact
  reopens its approval. Claude cannot approve for you.
- **A code gate.** Code edits wait for an approved intent, spec and plan whose hashes still match.
  Small changes can skip the ceremony with `sdlc trivial`.
- **A Definition of Done.** When code changed, the `Stop` hook runs your verify commands and sends
  Claude back on a failure.
- **Locked tests for bug fixes.** Tests written to reproduce a bug stay read-only until you unlock
  them, so the fix cannot bend the tests.
- **Deploy and push gates.** Merges, publishes, deploys and pushes to protected branches ask you
  first.
- **An independent reviewer.** The `sdlc-reviewer` agent reviews the diff against the spec, the
  plan and your `REVIEW.md` with fresh context.
- **A status bar.** A band above the prompt shows the feature, its stage and the one action that
  moves it on, such as `Approve spec`. The prompt's typeahead completes `sdlc ...` commands.

## Get started

```bash
claude plugin marketplace add Sennjen/claude-sdlc
claude plugin install ai-sdlc@ai-sdlc
```

Then run `/ai-sdlc:init` in your project. It detects the verify commands, adds a short
`CLAUDE.md` section and `REVIEW.md`, and writes `sdlc.config.json`, the switch that turns the
plugin on for that repository. Without `sdlc.config.json` the plugin does nothing.

Requirements: Claude Code 2.1.293 or newer for the status bar (the gates work on older
versions), `python3` 3.9 or newer, and `git`. The plugin is for Claude Code: it uses hooks, a
`bin/` command and a UI module, which claude.ai chat and Cowork don't load.

## What the plugin runs, reads and stores

Everything runs on your machine. The plugin makes no network requests and sends no data anywhere.

- **Runs:** `python3` with the plugin's own `scripts/sdlc.py` from its hooks and its UI module;
  `git` to read the repository (`git config user.name`, diffs and hashes of the change); the verify
  commands you list in `sdlc.config.json`, through your shell, when a turn that changed code ends.
  When you click a file or PR link in the bar, it opens the file in the desktop app's Files pane,
  or runs `open` or `xdg-open`.
- **Reads:** the repository, `sdlc.config.json`, and the output of the tools Claude runs, to find
  the URL from `gh pr create` or `glab mr create`.
- **Decides:** its hooks allow, deny or ask about Claude's tool calls (the gates above). It never
  changes your Claude Code settings or permission mode.
- **Stores, inside your repository:** the artifacts and `approvals.json` under `docs/sdlc/`, and
  its state in `.sdlc/` (the active feature, per-session flags, the test lock, review verdicts,
  PR URLs and an `audit.log`). Approvals and the audit log record your git user name, or your OS user name
  when git has none, and the time.

## More

The full documentation, with every gate, command and limit, is in the
[repository README](https://github.com/Sennjen/claude-sdlc#readme). Report problems in
[GitHub Issues](https://github.com/Sennjen/claude-sdlc/issues).

MIT License. See [LICENSE](LICENSE).
