# Privacy policy

Last updated: 2026-11-05

ai-sdlc is a Claude Code plugin by Yevhenii Dziubenko. It runs only on your computer. It has no
server, and its author receives no data from it.

## What the plugin stores

The plugin writes these files inside your repository:

- `docs/sdlc/<feature>/approvals.json`: each approval, with the sha256 of the approved file, your
  git user name (`git config user.name`), or your operating system user name when git has none,
  and the time.
- `.sdlc/`: the plugin's working state. It holds the active feature, per-session flags, the test
  lock, review verdicts, PR URLs and `audit.log`. The audit log records the time of each gate
  decision with the file or the first 300 characters of the shell command it was about, and the
  `sdlc ...` commands you type, such as approvals, with your user name.

`/ai-sdlc:init` adds `.sdlc/` to `.gitignore`, so the state and the audit log stay on your
computer. The artifacts under `docs/sdlc/` are meant to be committed. Once you commit and push
them, they go wherever the repository goes, like any other file in it.

## What the plugin sends

Nothing. The plugin makes no network requests. The status line it adds to each prompt becomes
part of your conversation with Claude, which Anthropic's privacy policy covers.

## How long data is kept

Until you delete it. Deleting `.sdlc/` and `docs/sdlc/` removes everything the plugin stored.

## Contact

Ask questions about this policy in [GitHub Issues](https://github.com/Sennjen/claude-sdlc/issues).
