# Sourced by fixture.sh of cases whose agent runs git (the review stage reads the diff).
# On macOS, /usr/bin/git is an xcrun shim. The eval sandbox denies xcrun's cache in the user
# temp dir, so the shim fails with "xcode-select: Failed to locate 'git'" and the agent either
# stops or works around it. The sandbox can run the real binary, and the agent's zsh reads
# $HOME/.zshenv, so define git there. Only touch a temporary HOME, never a real one.
dev="$(xcode-select -p 2>/dev/null || true)"
case "$HOME" in /tmp/*|/private/tmp/*)
  if [ -x "$dev/usr/bin/git" ]; then
    printf 'git() { "%s/usr/bin/git" "$@"; }\n' "$dev" >> "$HOME/.zshenv"
  fi
esac
