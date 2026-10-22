# AI-native SDLC for Claude Code and Codex

Claude Code writes code fast, and skips the parts of the job that keep a team safe: agreeing on
what to build, writing it down, and waiting for a human to say yes. This plugin puts those steps
back, and enforces them with hooks instead of asking nicely in a prompt.

Every change moves through **Intent → Spec → Plan → Build → Verify → Review**. Claude writes each
artifact under `docs/sdlc/<feature>/`, and you approve it. Until the plan is approved, Claude
cannot edit code. When it is done, the Definition of Done runs your verify commands, and
independent code and security reviewer agents check the change before the PR.

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
- **Independent reviewers.** The `sdlc-reviewer` agent reviews the diff against the spec, the
  plan and your `REVIEW.md` with fresh context. The `sdlc-security-reviewer` agent checks it
  separately for vulnerabilities the change introduces.
- **A status bar.** A band above the prompt shows the feature, its stage and the one action that
  moves it on, such as `Approve spec`. The prompt's typeahead completes `sdlc ...` commands.

## Get started

```bash
claude plugin marketplace add Sennjen/claude-sdlc
claude plugin install ai-sdlc@ai-sdlc
```

Then run `/ai-sdlc:init` in your project. It detects the verify commands, adds a short
`CLAUDE.md` section and `REVIEW.md`, and writes `sdlc.config.json`, the switch that turns the
plugin on for that repository. Without `sdlc.config.json` the plugin does nothing. After a plugin
update, `/ai-sdlc:init update` proposes what the new templates add to the project's `REVIEW.md`
and `sdlc.config.json`, and keeps the project's own rules.

Requirements: Claude Code 2.1.293 or newer for the status bar (the gates work on older
versions), `python3` 3.9 or newer, and `git`. The plugin is for Claude Code: it uses hooks, a
`bin/` command and a UI module, which claude.ai chat and Cowork don't load.

## Codex

Register the repository's `.agents/plugins/marketplace.json`, install `ai-sdlc@ai-sdlc`, review
and trust its hooks, then invoke `$ai-sdlc:init` in the target project. Codex uses `AGENTS.md`
and an inline status block with contextual buttons in chat on clients with inline visualization
support. Other clients use a text fallback. Use `$ai-sdlc:status` to refresh it. Old buttons
expire when their displayed state changes. Protected edits and gated deploys are denied and
require a user action outside the agent.

See the [Codex guide](https://github.com/Sennjen/claude-sdlc/blob/main/docs/codex.md) for local
installation and compatibility. The other sections of this README describe the Claude Code
interface.

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
