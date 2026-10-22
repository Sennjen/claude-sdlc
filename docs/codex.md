# Codex support

The Codex package shares the SDLC stages, artifacts, approvals, verification commands, and
test locks with the Claude Code package. It adds a native manifest, a repository marketplace,
and an adapter for Codex lifecycle hooks. Python 3.9+ and Git are required.

## Install from a local checkout

Register the checkout containing `.agents/plugins/marketplace.json`:

```bash
codex plugin marketplace add /absolute/path/to/claude-sdlc
codex plugin add ai-sdlc@ai-sdlc
```

These commands are supported by the CLI used for packaging verification (0.134.0). In a
desktop client with a Plugins directory, select the **AI-native SDLC** local marketplace
and install the plugin there. After plugin changes, refresh the marketplace and installed
copy using the client's supported plugin controls, then restart the client.

Review and trust the exact hook definitions before use (`/hooks` in the CLI). Changed
definitions require a new trust review. Project-local configuration also requires a trusted
project. Do not enable hooks by bypassing trust review.

In the target project, invoke `$ai-sdlc:init` (or select the plugin's **init** skill in the
skill picker). Init writes repository instructions to `AGENTS.md`, adds `REVIEW.md`, detects
verification commands, and writes `sdlc.config.json` last. It preserves existing instructions.
Commit the generated project files. The plugin stays inert without `sdlc.config.json`.
After a plugin update, `$ai-sdlc:init update` proposes what the new templates add to the
project's `REVIEW.md` and `sdlc.config.json`. Codex denies edits to these protected files, so
you apply the proposed text yourself.

Type approvals as ordinary chat messages, for example:

```text
sdlc approve intent
sdlc approve spec
sdlc approve plan
sdlc status
```

The shell CLI intentionally cannot approve stages. Use the absolute `bin/sdlc` path from the
`[SDLC]` context if the shell cannot find `sdlc`; Codex does not need to add `bin/` to PATH.

## Interactive status block in chat

Invoke `$ai-sdlc:status`, ask for the SDLC status, or type `sdlc status`. Codex renders a
compact interactive block in its own chat on clients that support inline visualizations.
The renderer uses an HTML fragment with actual buttons, embedded with the `visualize`
content reference. No browser tab, web server, or extra window is needed. The block shows:

- the active feature, stage approvals, code gate, and test lock;
- completed plan steps and the last verification and review results;
- document buttons that request opening artifacts in Codex, and a pull request link;
- the next action, verification, test unlock, feature switching, and refresh controls
  when those actions apply.

In an enabled repository, the prompt hook asks the primary agent to append a fresh card
to its final response. The read-only `sdlc chat --session ID --html` command produces the
fragment; the hook supplies the actual session ID. The agent saves the fragment in its
task's writable visualization directory outside the repository and emits a content reference
to that file. When no such directory is supplied, use `ARTIFACTS_DIR/_ui/`, which is excluded
from the code fingerprint. Saving among code files would expire the card's own token. Use
`--language uk` or `--language ru` to localize labels without changing action prompts.
Generating the fragment does not start a feature, approve anything, run checks, or modify
SDLC state. Saving it creates only a display file.

Buttons use the host's `window.openai.sendFollowUpMessage` bridge to request user messages
through the same prompt hook as typed commands. Approval, unlock, close, and feature-switch
buttons act only after the person clicks and confirms the request in the host.
Skill buttons request the corresponding existing workflow skill and preserve its gates.
The agent must never execute or activate a human-only button itself.

Each action is bound to the displayed repository, session, feature, artifact hashes, code,
and test-lock instance. If that state changed, the action is rejected and a fresh card is
shown; the old click is not replayed. Stage approval also rechecks the displayed feature
and document hash in the shared approval handler.

This is a message snapshot. Earlier cards do not update in place, and it is not a pinned
panel above the composer. Refresh or the next response shows new state. Verification
that runs in `Stop` after a response appears in the next card. Clients without inline
visualizations can show the status as text and accept the existing typed commands.
`sdlc status` in Codex now uses a model reply to render the card; Claude's command retains
its original behavior without a model turn.

The earlier `:codex-followup` Markdown approach rendered as a plain list in the user's
Codex client. It is retained only in the plain `sdlc chat` transport fallback; the status
skill and prompt hook use `--html`. Do not describe that text fallback as a button UI.
If the host bridge is absent or rejects a request, the block shows the command to type
instead of reporting that the action succeeded. It never runs shell commands or changes
workflow state from JavaScript.

## Existing projects

The existing `docs/sdlc/`, `.sdlc/`, and approval hashes remain compatible. Before using Codex,
manually merge the [Codex instructions](../plugins/ai-sdlc/skills/init/agents-md-snippet.md)
into `AGENTS.md`. Fill in the project's actual commands.

If `sdlc.config.json` defines `protected_paths`, append the Codex entries from the current
[configuration template](../plugins/ai-sdlc/skills/init/config-template.json). Explicit lists
override the defaults, so an older list does not automatically protect `AGENTS.md`,
`AGENTS.override.md`, `.codex/` configuration, and `.agents/` plugin or skill definitions.
Make these protected-file changes yourself outside the agent.

## Behavior differences

| Capability | Codex behavior |
| --- | --- |
| File changes | The adapter checks every add, update, delete, and rename path in `apply_patch`. A denied path blocks the whole patch. |
| Shell commands | Canonical `Bash` hook input is passed to the shared shell-write and deployment checks. Raw `exec_command` arguments are also normalized. |
| Protected files and deploys | A shared `ask` decision becomes `deny`. The user must make that edit or run that command outside the agent. A chat approval does not override this denial. |
| Status and approvals | An inline interactive block replaces the Claude bar on clients with visualization support. Guarded button requests and existing typed commands use the same prompt hook. |
| Plan mode | Present a plan while writes are unavailable; save it after leaving plan mode. The user still approves `plan.md` explicitly. |
| Independent reviews | The review skill gives the bundled code and security reviewer procedures to two fresh read-only subagents. Their reports start with `SDLC REVIEW: <slug>` and `SDLC SECURITY REVIEW: <slug>`; `SubagentStop` records each verdict only for that active feature. |
| Missing subagents | Report that independent review remains outstanding. Do not substitute a self-review. |
| PR tracking | `PostToolUse` records PR URLs from `gh pr create`, `glab mr create`, and supported forge MCP tools. |
| Failed verification | `Stop` continues the turn with the failure, up to the shared retry limit. |

Codex cannot use Claude's `hooks/ui.tsx` module or automatically load the Claude agent
profile. The Codex manifest explicitly selects `hooks/codex.json`, so the Claude hook
configuration and UI module are not loaded through it.

## Compatibility and limits

Use a local Codex client with the documented `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`SubagentStop`, and `Stop` events, including `apply_patch` tool coverage. Installing the
skills alone, disabling hooks, or leaving them untrusted removes automatic enforcement.
The CLI packaging smoke check does not prove that an older client implements every event.

Hooks are guardrails for a cooperative agent, not a sandbox. The shared shell parser has the
limits documented in the repository README. Custom file-writing tools and specialized tool
paths outside hook coverage are not intercepted. Hook timeouts, unavailable Python, or host
errors can skip checks. Use branch protection and required CI checks for branch guarantees.

This is a local/team marketplace package. OpenAI's current public directory rules exclude
plugins that contain lifecycle hooks. No plugin or workspace publication is performed by
the installation steps above.

The implementation follows official OpenAI documentation for
[plugin packaging](https://developers.openai.com/plugins/build/plugins) and
[lifecycle hooks](https://learn.chatgpt.com/docs/hooks). In particular, Codex currently does
not support `PreToolUse.permissionDecision: "ask"`; returning it is an error that can allow
the tool to continue. The adapter returns `deny` instead.

## Verification

Run the shared and Codex hook tests without model calls:

```bash
python3 -m unittest discover -s plugins/ai-sdlc/tests -v
```

With Node.js, Playwright, and its Chromium browser installed, also exercise the actual HTML
buttons in a headless browser. Host messages are mocked; this test sends nothing to Codex:

```bash
SDLC_CODEX_UI_BROWSER_CHECK=1 python3 -m unittest discover -s plugins/ai-sdlc/tests -v
```

This checks exact prompt forwarding, duplicate clicks while pending, rejected requests,
missing host APIs, and layout at 320px and 736px. It verifies the fragment behavior rather
than assuming a Markdown directive became a button. The host's real confirmation flow
still requires a human check in Codex.

To also install into a temporary profile and verify that the real Codex loader discovers
all nine skills and the five Codex hook events (requires Codex CLI):

```bash
SDLC_CODEX_PACKAGE_CHECK=1 python3 -m unittest discover -s plugins/ai-sdlc/tests -v
```

This check uses `plugin/read` and `skills/list` without starting a model turn. It does not
change your personal Codex configuration. The shared init skill retains Claude's
`disable-model-invocation` frontmatter; Codex's equivalent policy is in `agents/openai.yaml`.

For an interactive check after installation, use a disposable repository:

1. Trust the hooks and run the init skill. Confirm it creates `AGENTS.md` and
   `sdlc.config.json`.
2. Start a feature and attempt a code patch before approval. Confirm the tool is denied.
3. Approve intent, spec, and plan through chat. Confirm code edits now pass.
4. Change the approved plan. Confirm code edits wait for a fresh approval.
5. Lock tests, attempt a test edit, then type `sdlc unlock tests`. Confirm both outcomes.
6. Introduce a failing verification command and change code. Confirm Stop reports the
   failure and continues the agent, then stops retrying at the limit.
7. Run the review skill and inspect `bin/sdlc state` for the feature's `review` and `security`
   verdicts and PR URL.
8. Request a chat status card. Change an artifact, switch features, or relock tests, then
   click the corresponding old action. Confirm it does nothing and offers a fresh card.

Do not treat the hook fixtures or marketplace install check as an interactive client test.
