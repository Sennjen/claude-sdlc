#!/usr/bin/env python3
"""AI-native SDLC gatekeeper for Claude Code.

Hooks (stdin = hook JSON):   sdlc.py hook <prompt|pre-edit|pre-bash|post-edit|stop>
Agent-safe CLI:              sdlc.py cli <status|new|lock-tests|features>

The plugin is inert in any repository that has no sdlc.config.json.
Stage approvals are recorded only from real user prompts (UserPromptSubmit),
so the agent cannot approve its own intent, spec or plan.
"""
import datetime
import getpass
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

CONFIG_NAME = "sdlc.config.json"
STATE_DIR = ".sdlc"
STAGES = ("intent", "spec", "plan")
SKIPPABLE = ("spec",)
STAGE_LABEL = {"intent": "1 Plan (intent.md)", "spec": "2 Design (spec.md)",
               "plan": "3 Build: planning (plan.md)", "build": "3-5 Build / Verify / Review"}
STAGE_SKILL = {"intent": "sdlc:intent", "spec": "sdlc:spec", "plan": "sdlc:plan",
               "build": "sdlc:build, then sdlc:verify and sdlc:review"}
MAX_STOP_RETRIES = 2
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "artifacts_dir": "docs/sdlc",
    "verify": [],
    "verify_timeout_sec": 600,
    "ungated_paths": ["docs/**", "**/*.md", "**/*.mdx", ".gitignore"],
    # Files that steer the agent. Not all of .claude/: worktrees live in .claude/worktrees/.
    "protected_paths": [CONFIG_NAME, "**/.claude/settings*.json", "**/.claude/hooks/**",
                        "**/.claude/agents/**", "**/.claude/skills/**", "**/.claude/commands/**",
                        "**/.claude/rules/**", "**/.claude/output-styles/**", ".mcp.json",
                        "**/CLAUDE.md", "**/CLAUDE.local.md", "REVIEW.md"],
    "test_paths": ["**/test/**", "**/tests/**", "**/__tests__/**", "**/*.test.*",
                   "**/*.spec.*", "**/*_test.*", "**/test_*.py", "**/*Test.*", "**/*Tests.*"],
    "gated_commands": [r"\bgit\s+push\b", r"\bgh\s+pr\s+merge\b", r"\bnpm\s+publish\b",
                       r"\bterraform\s+apply\b", r"\bkubectl\s+(apply|delete|rollout)\b",
                       r"\bhelm\s+(install|upgrade)\b", r"\bvercel\b.*--prod\b",
                       r"\bfirebase\s+deploy\b", r"\beas\s+(submit|update)\b",
                       r"\bfastlane\b"],
    "gated_command_decision": "ask",
}

# --------------------------------------------------------------------------- utils


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def who():
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        if name:
            return name
    except Exception:
        pass
    return getpass.getuser()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def glob_to_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(rel, patterns):
    return any(glob_to_regex(p).match(rel) for p in patterns)


def find_root(start):
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, CONFIG_NAME)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


# ------------------------------------------------------------------------- project


class Project(object):
    def __init__(self, root):
        self.root = root
        cfg = dict(DEFAULTS)
        cfg.update(read_json(os.path.join(root, CONFIG_NAME), {}))
        self.cfg = cfg
        self.art_rel = cfg["artifacts_dir"].strip("/")
        self.art = os.path.join(root, self.art_rel)
        self.state = os.path.join(root, STATE_DIR)

    # -- paths
    def rel(self, path, cwd=None):
        """Repo-relative POSIX path, or None when the path is outside the repo."""
        if not os.path.isabs(path):
            path = os.path.join(cwd or self.root, path)
        path = os.path.realpath(os.path.expanduser(path))
        root = os.path.realpath(self.root)
        rel = os.path.relpath(path, root)
        if rel == ".." or rel.startswith(".." + os.sep):
            return None
        return rel.replace(os.sep, "/")

    def classify(self, rel):
        """state | approvals | artifact | protected | test | docs | code"""
        if rel == STATE_DIR or rel.startswith(STATE_DIR + "/"):
            return "state"
        if rel.startswith(self.art_rel + "/"):
            return "approvals" if rel.endswith("/approvals.json") else "artifact"
        if matches(rel, self.cfg["protected_paths"]):
            return "protected"
        if matches(rel, self.cfg["test_paths"]):
            return "test"
        if matches(rel, self.cfg["ungated_paths"]):
            return "docs"
        return "code"

    # -- features
    def fdir(self, slug):
        return os.path.join(self.art, slug)

    def features(self):
        if not os.path.isdir(self.art):
            return []
        return sorted(d for d in os.listdir(self.art)
                      if os.path.isfile(os.path.join(self.art, d, "intent.md")))

    def approvals(self, slug):
        return read_json(os.path.join(self.fdir(slug), "approvals.json"), {})

    def save_approvals(self, slug, data):
        write_json(os.path.join(self.fdir(slug), "approvals.json"), data)

    def is_done(self, slug):
        return "done" in self.approvals(slug)

    def active(self):
        try:
            with open(os.path.join(self.state, "active"), encoding="utf-8") as f:
                slug = f.read().strip()
            if slug:
                return slug
        except OSError:
            pass
        open_features = [f for f in self.features() if not self.is_done(f)]
        return open_features[0] if len(open_features) == 1 else None

    def set_active(self, slug):
        os.makedirs(self.state, exist_ok=True)
        path = os.path.join(self.state, "active")
        if slug:
            with open(path, "w", encoding="utf-8") as f:
                f.write(slug + "\n")
        elif os.path.exists(path):
            os.remove(path)

    def stage_state(self, slug, stage, approvals=None):
        """missing | draft | approved | stale | skipped"""
        a = (approvals if approvals is not None else self.approvals(slug)).get(stage)
        path = os.path.join(self.fdir(slug), stage + ".md")
        if a and a.get("skipped"):
            return "skipped"
        if not os.path.isfile(path):
            return "missing"
        if not a:
            return "draft"
        return "approved" if a.get("sha256") == sha256_file(path) else "stale"

    def current_stage(self, slug):
        approvals = self.approvals(slug)
        for stage in STAGES:
            st = self.stage_state(slug, stage, approvals)
            if st not in ("approved", "skipped"):
                return stage, st
        return "build", None

    # -- session / local state
    def session_path(self, sid):
        return os.path.join(self.state, "sessions", (sid or "default") + ".json")

    def session(self, sid):
        return read_json(self.session_path(sid), {})

    def save_session(self, sid, data):
        write_json(self.session_path(sid), data)

    def tests_locked(self):
        return os.path.exists(os.path.join(self.state, "tests-locked"))

    def audit(self, event, **kw):
        os.makedirs(self.state, exist_ok=True)
        entry = {"at": now(), "event": event}
        entry.update(kw)
        with open(os.path.join(self.state, "audit.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- status text
    def status(self, sid=None, verbose=False):
        lines = []
        slug = self.active()
        sess = self.session(sid) if sid else {}
        if not slug:
            lines.append("[SDLC] No active feature. New feature or bugfix work starts with the "
                         "sdlc:intent skill (`sdlc new <slug>`). Code edits are blocked until a plan "
                         "is approved. For a trivial change ask the user to type `sdlc trivial`.")
        else:
            approvals = self.approvals(slug)
            states = ", ".join("%s=%s" % (s, self.stage_state(slug, s, approvals)) for s in STAGES)
            stage, st = self.current_stage(slug)
            line = "[SDLC] Feature `%s` (%s) -> stage %s." % (slug, states, STAGE_LABEL[stage])
            if stage == "build":
                line += " Code gate OPEN. Follow plan.md; use %s." % STAGE_SKILL[stage]
            else:
                action = "write %s.md" % stage if st == "missing" else (
                    "finish %s.md, then ask the user to type `sdlc approve %s`" % (stage, stage))
                if st == "stale":
                    action = "%s.md changed after approval; ask the user to re-approve with " \
                             "`sdlc approve %s`" % (stage, stage)
                line += " Code gate CLOSED. Next: %s (skill %s)." % (action, STAGE_SKILL[stage])
            lines.append(line)
        if sess.get("fasttrack"):
            lines.append("[SDLC] Fast-track (trivial change) is active for this session: code gate "
                         "open, verification still required.")
        if self.tests_locked():
            lines.append("[SDLC] Test files are LOCKED: fix the code, not the tests.")
        if verbose:
            others = [f for f in self.features() if f != slug]
            if others:
                lines.append("[SDLC] Other features: " + ", ".join(
                    "%s%s" % (f, " (done)" if self.is_done(f) else "") for f in others))
            if self.cfg["verify"]:
                lines.append("[SDLC] Verify commands: " + " && ".join(self.cfg["verify"]))
        return "\n".join(lines)


# ------------------------------------------------------------------- hook helpers


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def pre_decision(decision, reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                 "permissionDecisionReason": reason}})


def pre_context(context):
    """Add context for Claude without touching the normal permission flow."""
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}})


def load_hook():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    start = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
    root = find_root(start) or find_root(os.environ.get("CLAUDE_PROJECT_DIR"))
    if not root:
        sys.exit(0)  # SDLC not enabled in this repository
    return data, Project(root)


def code_gate(p, sid):
    """Return None when code edits are allowed, otherwise the reason they are not."""
    if p.session(sid).get("fasttrack"):
        return None
    slug = p.active()
    if not slug:
        return ("No active SDLC feature. Start with the sdlc:intent skill (`sdlc new <slug>`), "
                "or, for a trivial change, ask the user to type `sdlc trivial`.")
    stage, st = p.current_stage(slug)
    if stage == "build":
        return None
    return ("Feature `%s` is at stage %s (%s.md is %s). Code edits open only after the user "
            "approves intent, spec and plan (`sdlc approve <stage>`). Continue with skill %s."
            % (slug, STAGE_LABEL[stage], stage, st, STAGE_SKILL[stage]))


def check_target(p, rel, sid, via_bash=False):
    """Return (decision, reason) for writing to a repo-relative path."""
    kind = p.classify(rel)
    if kind == "state":
        return "deny", ".sdlc/ is managed by the SDLC hooks. Use the `sdlc` CLI or ask the user."
    if kind == "approvals":
        return "deny", ("approvals.json is written only when the user types `sdlc approve <stage>` "
                        "in chat. Ask the user to approve.")
    if kind == "artifact":
        parts = rel[len(p.art_rel) + 1:].split("/")
        if len(parts) == 2 and parts[1] in ("spec.md", "plan.md"):
            slug, name = parts
            approvals = p.approvals(slug)
            if p.stage_state(slug, "intent", approvals) != "approved":
                return "deny", ("intent.md of `%s` is not approved. Ask the user to review it and "
                                "type `sdlc approve intent` before writing %s." % (slug, name))
            if name == "plan.md" and p.stage_state(slug, "spec", approvals) not in ("approved", "skipped"):
                return "deny", ("spec.md of `%s` is not approved. Ask the user to type `sdlc approve "
                                "spec` (or `sdlc skip spec <reason>` for small changes)." % slug)
        return "allow", None
    if kind == "protected":
        return "ask", "%s controls the SDLC or agent behaviour; the user must confirm this edit." % rel
    if kind == "test" and p.tests_locked():
        return "deny", ("Test files are locked (bugfix flow: the failing test is the contract). Fix "
                        "the code, not the test. Only the user can type `sdlc unlock tests`.")
    if kind in ("docs", "test"):
        return "allow", None
    reason = code_gate(p, sid)
    if reason:
        if via_bash:
            reason = "Shell write to %s blocked. %s" % (rel, reason)
        return "deny", reason
    return "allow", None


# ------------------------------------------------------------------ shell parsing

REDIRECT = re.compile(r"(?:^|[^<>&0-9])[0-9]*>{1,2}\|?\s*(?!&)([^\s;&|<>()]+)")
SAFE_SINKS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")
TMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "$TMPDIR", "${TMPDIR")
INPLACE = re.compile(r"^(sed|gsed|perl|ruby)$")


def split_segments(cmd):
    return [s for s in re.split(r"&&|\|\||[;|\n]", cmd) if s.strip()]


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)^\s*\2\s*$", re.S | re.M)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def strip_literals(cmd):
    """Drop heredoc bodies and quoted strings so `a => b` inside them is not a redirect."""
    cmd = HEREDOC.sub(lambda m: "<<" + m.group(3), cmd)
    return QUOTED.sub("''", cmd)


def bash_write_targets(cmd):
    """Best-effort list of paths a shell command writes to ("?" means unknown)."""
    targets = []
    for m in REDIRECT.finditer(strip_literals(cmd)):
        targets.append(m.group(1))
    for seg in split_segments(HEREDOC.sub(lambda m: "<<" + m.group(3), cmd)):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            tokens = seg.split()
        while tokens and re.match(r"^\w+=", tokens[0]):
            tokens.pop(0)
        if not tokens:
            continue
        prog = os.path.basename(tokens[0])
        args = tokens[1:]
        if prog == "tee":
            targets += [a for a in args if not a.startswith("-")]
        elif INPLACE.match(prog) and any(re.match(r"^-[a-zA-Z]*i", a) for a in args):
            positional, skip = [], False
            for a in args:
                if skip:
                    skip = False
                    continue
                if a in ("-e", "-f", "--expression"):
                    skip = True
                    continue
                if not a.startswith("-"):
                    positional.append(a)
            has_expr = any(a in ("-e", "--expression") for a in args)
            targets += positional if has_expr else positional[1:]
        elif prog in ("cp", "mv", "install", "rsync", "ln") and args:
            positional = [a for a in args if not a.startswith("-")]
            if len(positional) >= 2:
                targets.append(positional[-1])
        elif prog in ("patch",) or (prog == "git" and args[:1] == ["apply"]):
            targets.append("?")
        elif prog in ("touch", "truncate"):
            targets += [a for a in args if not a.startswith("-")]
        elif prog == "dd":
            targets += [a[3:] for a in args if a.startswith("of=")]
    clean = []
    for t in targets:
        t = t.strip("'\"")
        if not t or t in SAFE_SINKS or t.startswith(TMP_PREFIXES):
            continue
        clean.append(t)
    return clean


SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "ssh", "su", "watch")
RUNNER = re.compile(r"^(?:(?:ba|z|da|k)?sh|fish|python[\d.]*|node|deno|bun|tsx|ts-node|ruby|perl|php"
                    r"|sudo|env|nohup|exec|command|xargs|time|if|then|else|elif|do|while|until|!)$")
SUBSTITUTION = re.compile(r"\$\(|`")
PATHLIKE = re.compile(r"/|\.\w+$")
MARK = re.compile(r"\x00(\d+)\x00")


def program(token):
    return os.path.basename(token.lstrip("$({`"))


def run_positions(tokens):
    """Indexes of the tokens that name what runs: the program and the word after a runner
    (bash, python3, node, tsx, sudo, xargs, `do`...), e.g. `deploy.sh` in `bash deploy.sh`."""
    out, expect = set(), True
    for i, t in enumerate(tokens):
        if t.startswith("-") or re.match(r"^\w+=", t):
            continue
        if expect:
            out.add(i)
        expect = bool(RUNNER.match(program(t)))
    return out


def inside_name(token, start, end):
    """True when token[start:end] is only part of a file or directory name in a path."""
    if not PATHLIKE.search(token) or "/" in token[start:end]:
        return False
    return token.find("/", end) != -1 or (start, end) != (token.rfind("/", 0, start) + 1, len(token))


def invokes(pattern, tokens):
    """True when pattern matches the command itself, not just a word in a file name argument."""
    spans, pos = [], 0
    for t in tokens:
        spans.append((pos, pos + len(t)))
        pos += len(t) + 1
    runs = run_positions(tokens)
    for m in re.finditer(pattern, " ".join(tokens)):
        s, e = m.span()
        i = next((i for i, (a, b) in enumerate(spans) if a <= s and e <= b), None)
        if i is None or i in runs or not inside_name(tokens[i], s - spans[i][0], e - spans[i][0]):
            return True
    return False


def gated_pattern(cmd, patterns):
    """First gated pattern the command runs, or None. Heredoc bodies and quoted text with
    spaces count only when the command runs a shell (`bash -c`, `ssh host '...'`, `| sh`,
    `sh <<EOF`) or the shell expands `$(...)` in them."""
    literals = []

    def hide(text, expands):
        literals.append((text, expands))
        return "\x00%d\x00" % (len(literals) - 1)

    def quoted(m):
        s = m.group(0)
        return hide(s[1:-1], s[0] == '"') if re.search(r"\s", s) else s

    def scan(text, depth):
        if depth > 4:  # pathological nesting: fall back to a plain search
            return next((p for p in patterns if re.search(p, text)), None)
        text = HEREDOC.sub(lambda m: "<<" + hide(m.group(4), not m.group(1)) + m.group(3), text)
        segments = split_segments(QUOTED.sub(quoted, text))
        shell = any(program(t) in SHELLS for seg in segments for t in seg.split())
        for seg in segments:
            tokens = [MARK.sub("''", t) for t in seg.split()]
            found = next((p for p in patterns if invokes(p, tokens)), None)
            for n in MARK.findall(seg):
                body, expands = literals[int(n)]
                if not found and (shell or expands and SUBSTITUTION.search(body)):
                    found = scan(body, depth + 1)
            if found:
                return found
        return None

    return scan(cmd, 0) if patterns else None


# -------------------------------------------------------------------- user commands

USER_CMD = re.compile(r"^\s*sdlc\s+([a-z-]+)(?:\s+(.*))?$", re.I)
USER_ONLY = ("approve", "skip", "trivial", "unlock", "feature", "done")
NESTED_USER_CMD = re.compile(r"\bsdlc\s+(?:%s)\b" % "|".join(USER_ONLY), re.I)
CLAUDE_CLI = [r"\bclaude(?:-code)?\b"]


def handle_user_command(p, sid, line):
    m = USER_CMD.match(line)
    if not m:
        return None
    verb, rest = m.group(1).lower(), (m.group(2) or "").strip()
    args = rest.split()
    user = who()

    if verb == "approve":
        stage = args[0].lower() if args else ""
        slug = p.active()
        if stage not in STAGES:
            return "[SDLC] Usage: `sdlc approve intent|spec|plan`. Tell the user."
        if not slug:
            return "[SDLC] Approval failed: no active feature. Tell the user."
        path = os.path.join(p.fdir(slug), stage + ".md")
        if not os.path.isfile(path):
            return "[SDLC] Approval failed: %s does not exist. Tell the user." % p.rel(path)
        approvals = p.approvals(slug)
        for prev in STAGES[:STAGES.index(stage)]:
            if p.stage_state(slug, prev, approvals) not in ("approved", "skipped"):
                return ("[SDLC] Approval failed: %s must be approved first "
                        "(it is %s). Tell the user." % (prev, p.stage_state(slug, prev, approvals)))
        digest = sha256_file(path)
        old = approvals.get(stage)
        invalidated = []
        if old and old.get("sha256") != digest:
            for later in STAGES[STAGES.index(stage) + 1:]:
                if later in approvals:
                    invalidated.append(later)
                    del approvals[later]
        approvals[stage] = {"sha256": digest, "by": user, "at": now()}
        p.save_approvals(slug, approvals)
        p.audit("approve", feature=slug, stage=stage, by=user, sha256=digest)
        msg = "[SDLC] The user (%s) APPROVED %s.md of `%s` (sha256 %s)." % (user, stage, slug, digest[:12])
        if invalidated:
            msg += " Approvals of %s were reset because an upstream artifact changed." % ", ".join(invalidated)
        nxt, _ = p.current_stage(slug)
        msg += " Next: %s (skill %s). Remind the user to commit the artifacts." % (STAGE_LABEL[nxt], STAGE_SKILL[nxt])
        return msg

    if verb == "skip":
        stage = args[0].lower() if args else ""
        reason = " ".join(args[1:]) or "not given"
        slug = p.active()
        if stage not in SKIPPABLE:
            return "[SDLC] Only `sdlc skip spec <reason>` is allowed. Tell the user."
        if not slug or p.stage_state(slug, "intent") != "approved":
            return "[SDLC] Skip failed: the intent must be approved first. Tell the user."
        approvals = p.approvals(slug)
        approvals[stage] = {"skipped": True, "reason": reason, "by": user, "at": now()}
        p.save_approvals(slug, approvals)
        p.audit("skip", feature=slug, stage=stage, by=user, reason=reason)
        return "[SDLC] The user skipped %s for `%s` (%s). Next: write plan.md (skill sdlc:plan)." % (stage, slug, reason)

    if verb == "trivial":
        sess = p.session(sid)
        if args and args[0].lower() in ("off", "end", "stop"):
            sess.pop("fasttrack", None)
            p.save_session(sid, sess)
            p.audit("fasttrack-off", session=sid, by=user)
            return "[SDLC] Fast-track ended by the user. Code gate follows the active feature again."
        sess["fasttrack"] = {"by": user, "at": now(), "reason": rest or "not given"}
        p.save_session(sid, sess)
        p.audit("fasttrack", session=sid, by=user, reason=rest)
        return ("[SDLC] The user enabled fast-track for this session (trivial change: %s). Code edits "
                "are allowed without intent/spec/plan. Keep the change minimal, still run verification "
                "(skill sdlc:verify). If it grows beyond trivial, stop and propose `sdlc new <slug>`."
                % (rest or "no reason given"))

    if verb == "unlock":
        path = os.path.join(p.state, "tests-locked")
        if os.path.exists(path):
            os.remove(path)
        p.audit("unlock-tests", by=user)
        return "[SDLC] The user unlocked test files."

    if verb == "feature":
        slug = args[0] if args else ""
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", slug):
            return "[SDLC] Usage: `sdlc feature <slug>` (lowercase, digits, dashes). Tell the user."
        p.set_active(slug)
        p.audit("activate", feature=slug, by=user)
        exists = os.path.isfile(os.path.join(p.fdir(slug), "intent.md"))
        return "[SDLC] Active feature set to `%s` by the user.%s" % (
            slug, "" if exists else " It has no intent.md yet: run `sdlc new %s` (skill sdlc:intent)." % slug)

    if verb == "done":
        slug = p.active()
        if not slug:
            return "[SDLC] Nothing to close: no active feature."
        approvals = p.approvals(slug)
        approvals["done"] = {"by": user, "at": now()}
        p.save_approvals(slug, approvals)
        p.set_active(None)
        p.audit("done", feature=slug, by=user)
        return "[SDLC] The user closed feature `%s`. Remind them to commit approvals.json." % slug

    if verb == "status":
        return "[SDLC] The user asked for the SDLC status. Show it to them:"

    return ("[SDLC] Unknown command `sdlc %s`. User commands: approve <intent|spec|plan>, skip spec "
            "<reason>, trivial [reason|off], unlock tests, feature <slug>, done, status." % verb)


# --------------------------------------------------------------------- fingerprint


def git(root, *args):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=30)
    return r.returncode, r.stdout


def fingerprint(p):
    """Hash of changed code files in the work tree; None when not a git repo."""
    rc, _ = git(p.root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return None
    names = set()
    rc, out = git(p.root, "diff", "--name-only", "-z", "HEAD")
    if rc != 0:
        rc, out = git(p.root, "ls-files", "-m", "-z")
    names.update(n.decode() for n in out.split(b"\0") if n)
    _, out = git(p.root, "ls-files", "-o", "--exclude-standard", "-z")
    names.update(n.decode() for n in out.split(b"\0") if n)
    _, top = git(p.root, "rev-parse", "--show-toplevel")
    top = top.decode().strip() or p.root
    h = hashlib.sha256()
    count = 0
    for name in sorted(names):
        rel = p.rel(os.path.join(top, name))
        if rel is None or p.classify(rel) in ("state", "approvals", "artifact", "docs"):
            continue
        count += 1
        h.update(rel.encode() + b"\0")
        full = os.path.join(p.root, rel)
        if os.path.isfile(full):
            h.update(sha256_file(full).encode())
        else:
            h.update(b"<deleted>")
    return h.hexdigest() if count else "clean"


def run_verify(p):
    """Run configured verify commands. Returns (ok, report)."""
    for cmd in p.cfg["verify"]:
        try:
            r = subprocess.run(cmd, shell=True, cwd=p.root, capture_output=True, text=True,
                               timeout=p.cfg["verify_timeout_sec"])
        except subprocess.TimeoutExpired:
            return False, "`%s` timed out after %ss." % (cmd, p.cfg["verify_timeout_sec"])
        if r.returncode != 0:
            tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-60:])
            return False, "`%s` exited with %d:\n%s" % (cmd, r.returncode, tail)
    return True, "passed: " + " && ".join(p.cfg["verify"])


# ------------------------------------------------------------------------- hooks


def hook_prompt(data, p):
    sid = data.get("session_id")
    sess = p.session(sid)
    if "baseline_fp" not in sess:
        sess["baseline_fp"] = fingerprint(p)
        p.save_session(sid, sess)
    parts = []
    first_line = (data.get("prompt") or "").strip().splitlines()[:1]
    if first_line:
        msg = handle_user_command(p, sid, first_line[0])
        if msg:
            parts.append(msg)
    parts.append(p.status(sid, verbose=bool(parts)))
    emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                 "additionalContext": "\n".join(parts)}})


def hook_pre_edit(data, p):
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if not path:
        sys.exit(0)
    rel = p.rel(path, data.get("cwd"))
    if rel is None:
        sys.exit(0)
    sid = data.get("session_id")
    decision, reason = check_target(p, rel, sid)
    if decision == "allow":
        m = re.match(r"^%s/([a-z0-9][a-z0-9-]*)/intent\.md$" % re.escape(p.art_rel), rel)
        if m:
            active = p.active()
            if not active or p.is_done(active):
                p.set_active(m.group(1))
                pre_context("[SDLC] `%s` is now the active feature." % m.group(1))
            elif active != m.group(1):
                pre_context("[SDLC] Active feature is still `%s`. To switch, the user types "
                            "`sdlc feature %s`." % (active, m.group(1)))
        sys.exit(0)
    p.audit("gate-" + decision, tool=data.get("tool_name"), path=rel, reason=reason)
    pre_decision(decision, reason)


def hook_pre_bash(data, p):
    cmd = (data.get("tool_input") or {}).get("command") or ""
    sid = data.get("session_id")
    if re.search(r"approvals\.json|(^|[\s/'\"=])\.sdlc(/|\b)|sdlc\.py", cmd):
        p.audit("gate-deny", tool="Bash", command=cmd[:300], reason="sdlc state")
        pre_decision("deny", "SDLC state (.sdlc/, approvals.json) is managed by hooks. Use the `sdlc` "
                             "CLI (status, new, lock-tests) or ask the user.")
    if NESTED_USER_CMD.search(cmd) and gated_pattern(cmd, CLAUDE_CLI):
        p.audit("gate-deny", tool="Bash", command=cmd[:300], reason="nested user command")
        pre_decision("deny", "User-only `sdlc` commands count only when the user types them in this "
                             "chat; sending one through another Claude session is blocked. Ask the user.")
    pattern = gated_pattern(cmd, p.cfg["gated_commands"])
    if pattern:
        decision = p.cfg["gated_command_decision"]
        p.audit("gate-" + decision, tool="Bash", command=cmd[:300], reason=pattern)
        pre_decision(decision, "SDLC deploy gate: `%s` needs explicit user approval. Make sure "
                               "verification passed and the PR review (skill sdlc:review) is done." % cmd[:120])
    cwd = data.get("cwd") or p.root
    scratch = data.get("scratchpad_dir")
    for target in bash_write_targets(cmd):
        if target == "?":
            reason = code_gate(p, sid)
            if reason:
                pre_decision("deny", "Applying patches is blocked. " + reason)
            continue
        if scratch and os.path.abspath(os.path.join(cwd, target)).startswith(os.path.abspath(scratch)):
            continue
        rel = p.rel(target, cwd)
        if rel is None:
            continue
        decision, reason = check_target(p, rel, sid, via_bash=True)
        if decision == "allow":
            continue
        if decision == "ask":
            pre_decision("ask", reason)
        p.audit("gate-deny", tool="Bash", command=cmd[:300], path=rel, reason=reason)
        pre_decision("deny", reason + " Prefer the Edit/Write tools for file changes.")
    sys.exit(0)


def hook_post_edit(data, p):
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    rel = p.rel(path, data.get("cwd")) if path else None
    if rel and p.classify(rel) in ("code", "test", "protected"):
        sid = data.get("session_id")
        sess = p.session(sid)
        if not sess.get("dirty"):
            sess["dirty"] = True
            p.save_session(sid, sess)
    sys.exit(0)


def hook_stop(data, p):
    if not p.cfg["verify"]:
        sys.exit(0)
    sid = data.get("session_id")
    sess = p.session(sid)
    fp = fingerprint(p)
    if fp is None:
        changed = bool(sess.get("dirty"))
    else:
        changed = fp != "clean" and fp != sess.get("baseline_fp") and fp != sess.get("last_pass_fp")
    if not changed:
        sys.exit(0)
    if fp is not None and fp == sess.get("last_fail_fp") and sess.get("fail_count", 0) >= MAX_STOP_RETRIES:
        emit({"systemMessage": "SDLC: verification is still failing after %d attempts; stopping so "
                               "a human can look. Do not treat this task as done." % MAX_STOP_RETRIES})
    ok, report = run_verify(p)
    if ok:
        sess.update({"last_pass_fp": fp, "dirty": False, "fail_count": 0})
        sess.pop("last_fail_fp", None)
        p.save_session(sid, sess)
        p.audit("verify-pass", session=sid)
        emit({"systemMessage": "SDLC verify " + report})
    sess["fail_count"] = sess.get("fail_count", 0) + 1 if fp == sess.get("last_fail_fp") else 1
    sess["last_fail_fp"] = fp
    p.save_session(sid, sess)
    p.audit("verify-fail", session=sid, report=report[:500])
    emit({"decision": "block",
          "reason": "SDLC Definition of Done not met. Verification failed: %s\n\nFix the cause "
                    "(not the tests), re-run the checks yourself, then finish." % report})


# --------------------------------------------------------------------------- CLI

TEMPLATE_INTENT = os.path.join(PLUGIN_ROOT, "skills", "intent", "template.md")


def cli(argv):
    root = find_root(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()) or find_root(os.getcwd())
    if not root:
        print("SDLC is not enabled here (no %s). Only the user can enable it, by running "
              "`/sdlc:init`." % CONFIG_NAME)
        return 1
    p = Project(root)
    verb = argv[0] if argv else "status"

    if verb == "status":
        print(p.status(verbose=True))
        return 0
    if verb == "features":
        active = p.active()
        for f in p.features():
            stage, st = p.current_stage(f)
            print("%s %-30s stage=%s%s%s" % ("*" if f == active else " ", f, stage,
                                              " (%s)" % st if st else "", " done" if p.is_done(f) else ""))
        return 0
    if verb == "new":
        if len(argv) < 2 or not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", argv[1]):
            print("Usage: sdlc new <slug> [feature|bugfix|incident]  (slug: lowercase, digits, dashes)")
            return 2
        slug, kind = argv[1], (argv[2] if len(argv) > 2 else "feature")
        dest = os.path.join(p.fdir(slug), "intent.md")
        if os.path.exists(dest):
            print("%s already exists." % p.rel(dest))
            return 1
        os.makedirs(p.fdir(slug), exist_ok=True)
        with open(TEMPLATE_INTENT, encoding="utf-8") as f:
            text = f.read()
        text = (text.replace("{{feature}}", slug).replace("{{author}}", who())
                .replace("{{created}}", now()).replace("{{type}}", kind))
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        active = p.active()
        if not active or p.is_done(active) or active == slug:
            p.set_active(slug)
            note = "Active feature: %s." % slug
        else:
            note = "Active feature is still `%s`; the user switches with `sdlc feature %s`." % (active, slug)
        print("Created %s. %s" % (p.rel(dest), note))
        return 0
    if verb == "lock-tests":
        os.makedirs(p.state, exist_ok=True)
        with open(os.path.join(p.state, "tests-locked"), "w") as f:
            f.write(now() + "\n")
        p.audit("lock-tests")
        print("Test files locked. Only the user can unlock them (`sdlc unlock tests` in chat).")
        return 0
    if verb in USER_ONLY:
        print("`sdlc %s` is a user-only command. Ask the user to type `sdlc %s ...` in the chat."
              % (verb, verb))
        return 1
    print("Usage: sdlc <status|features|new <slug> [type]|lock-tests>")
    return 2


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "hook":
        data, p = load_hook()
        handler = {"prompt": hook_prompt, "pre-edit": hook_pre_edit, "pre-bash": hook_pre_bash,
                   "post-edit": hook_post_edit, "stop": hook_stop}.get(sys.argv[2])
        if handler:
            try:
                handler(data, p)
            except SystemExit:
                raise
            except Exception as exc:  # never break the session on a hook bug
                sys.stderr.write("sdlc hook error: %r\n" % exc)
                sys.exit(0)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "cli":
        sys.exit(cli(sys.argv[2:]))
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
