#!/usr/bin/env python3
"""AI-native SDLC gatekeeper for Claude Code.

Hooks (stdin = hook JSON):   sdlc.py hook <prompt|pre-edit|pre-bash|post-edit|post-tool|subagent-stop|stop>
Agent-safe CLI:              sdlc.py cli <status|state|new|lock-tests|features>

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
    "gated_commands": [r"\bgit(?:\s+-[Cc]\s+\S+|\s+--?[\w.-]+(?:=\S+)?)*\s+push\b", r"\bgh\s+pr\s+merge\b", r"\bnpm\s+publish\b",
                       r"\bterraform\s+apply\b", r"\bkubectl\s+(apply|delete|rollout)\b",
                       r"\bhelm\s+(install|upgrade)\b", r"\bvercel\b.*--prod\b",
                       r"\bfirebase\s+deploy\b", r"\beas\s+(submit|update)\b",
                       r"\bfastlane\b"],
    "gated_command_decision": "ask",
    # A `git push` that only updates other branches (no force, delete or tags) needs no approval.
    "protected_branches": ["main", "master", "release/*"],
    # What pre-edit and pre-bash do when the hook itself fails: "deny" (fail closed) or "allow".
    "on_hook_error": "deny",
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

    # Local, per feature: the last review verdict and the PR, for the SDLC bar. Only hooks
    # write them (the agent cannot write .sdlc/); nothing gates on them.
    def feature_path(self, slug):
        return os.path.join(self.state, "features", slug + ".json")

    def feature_state(self, slug):
        return read_json(self.feature_path(slug), {})

    def save_feature_state(self, slug, data):
        write_json(self.feature_path(slug), data)

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

    def last_verify(self, sid):
        """The newest verify-pass/verify-fail audit entry of a session, or None."""
        try:
            with open(os.path.join(self.state, "audit.log"), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 65536))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("event") in ("verify-pass", "verify-fail") and entry.get("session") == sid:
                return {"ok": entry["event"] == "verify-pass", "at": entry.get("at"),
                        "report": entry.get("report")}
        return None

    def state_dict(self, sid=None):
        """Machine-readable status for UIs (hooks/ui.tsx). Read-only."""
        slug = self.active()
        out = {"enabled": True, "root": self.root, "active": slug, "stage": None,
               "stage_state": None, "stage_label": None, "next_skill": None, "code_gate": "closed",
               "artifact": None, "stages": {}, "features": [], "tests_locked": self.tests_locked(),
               "fasttrack": self.session(sid).get("fasttrack") if sid else None,
               "verify": {"commands": self.cfg["verify"], "last": self.last_verify(sid) if sid else None},
               "review": None, "pr": None}
        if slug:
            approvals = self.approvals(slug)
            for s in STAGES:
                a = approvals.get(s) or {}
                path = os.path.join(self.fdir(slug), s + ".md")
                out["stages"][s] = {"state": self.stage_state(slug, s, approvals),
                                    "path": self.rel(path),
                                    "sha256": sha256_file(path) if os.path.isfile(path) else None,
                                    "approved_sha256": a.get("sha256"), "by": a.get("by"),
                                    "at": a.get("at"), "reason": a.get("reason")}
            stage, st = self.current_stage(slug)
            out.update({"stage": stage, "stage_state": st, "stage_label": STAGE_LABEL[stage],
                        "next_skill": STAGE_SKILL[stage], "done": "done" in approvals})
            if stage in STAGES:
                out["artifact"] = out["stages"][stage]["path"]
            local = self.feature_state(slug)
            review = local.get("review")
            if review:
                # A review covers the change it read: any later code change makes it stale.
                current = stage == "build" and review.get("change") == fingerprint(self, base_ref(self))
                out["review"] = {"verdict": review.get("verdict"), "at": review.get("at"),
                                 "current": current}
            out["pr"] = local.get("pr")
        for f in self.features():
            stage, st = self.current_stage(f)
            out["features"].append({"slug": f, "stage": stage, "stage_state": st,
                                    "done": self.is_done(f)})
        if out["fasttrack"] or code_gate(self, sid) is None:
            out["code_gate"] = "open"
        return out


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


QUOTE_MARK = re.compile(r"\x01(\d+)\x01")
SHELL_VAR = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
SEPARATOR = re.compile(r"(&&|\|\||[;|\n])")
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
ASSIGNED = re.compile(r"(?<![\w$])([A-Za-z_]\w*)\+?=|\$\{([A-Za-z_]\w*):?[=+]")
# Past any of these a variable may hold another value (or an assignment may not have run):
# compound commands, subshells, substitutions, and builtins that set variables or $PWD.
COMPOUND = re.compile(r"[()`]|(?:^|\s)(?:[{}]|if|then|else|elif|fi|for|while|until|do|done|case|esac|select"
                      r"|function|eval|source|\.|declare|typeset|local|readonly|read|unset|mapfile"
                      r"|readarray|getopts|let|printf|cd|pushd|popd|wait|coproc|exec)(?=\s|$)")


def hide_quotes(text):
    """Quoted strings -> \\x01N\\x01 markers, so `;`, `|` and `>` inside them stay text.
    A raw \\x01 in the command is replaced first, so no marker can be forged."""
    quoted = []

    def keep(m):
        quoted.append(m.group(0))
        return "\x01%d\x01" % (len(quoted) - 1)
    return QUOTED.sub(keep, text.replace("\x01", "�")), quoted


def shell_word(token, quoted, env):
    """A word of hidden text as the shell reads it: known variables expanded (never inside
    single quotes) and quotes removed. Unknown variables stay as written."""
    def expand(text):
        return SHELL_VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), text)

    def unquote(m):
        q = quoted[int(m.group(1))]
        return q[1:-1] if q[0] == "'" else expand(q[1:-1])
    return QUOTE_MARK.sub(unquote, expand(token))


def literal_value(value, quoted):
    """The text of an assigned value that expands nothing, or None."""
    double = [quoted[int(n)] for n in QUOTE_MARK.findall(value) if quoted[int(n)][0] == '"']
    if re.search(r"[$`]", value) or any(re.search(r"[$`]", q) for q in double):
        return None
    text = shell_word(value, quoted, {})
    return text if text and not re.search(r"[\s*?\[]", text) else None


def split_words(seg):
    try:
        return shlex.split(seg)
    except ValueError:
        return seg.split()


SUFFIX = re.compile(r"^[.~_][^/\s]*$")
# What the shell expands in a word before a program sees it: a word with any of these is not
# the path its text spells.
EXPANDS = re.compile(r"[$`*?\[{~]")
# GNU sed long options a script or value hangs on, with the shortest prefix getopt_long
# takes for each (`--f` is ambiguous with --follow-symlinks).
SED_LONG = {"in-place": 1, "expression": 1, "file": 2, "line-length": 1}
# perl/ruby letters whose value is the rest of the cluster (`-Ilib`, `-Mstrict`, `-rjson`),
# and letters followed by optional digits after which the cluster goes on (`-0777`, `-l`).
VALUE_LETTERS = {"perl": "IMmxdDF", "ruby": "IrCEFKx"}
DIGIT_LETTERS = {"perl": "0lC", "ruby": "0TW"}


def sed_long(name):
    return next((opt for opt, least in SED_LONG.items() if len(name) >= least and opt.startswith(name)), None)


def inplace_targets(prog, args, literal, exists):
    """Files an in-place edit writes, as words of hidden text: `sed`/`gsed` with -i, -I or
    --in-place, `perl`/`ruby` with -i. None when nothing is edited in place; ["?"] when no
    file is left. `literal(word)` is the word without quotes and with nothing expanded;
    `exists(word)` says whether it names an existing path (unknown words count as existing).

    BSD sed takes a backup suffix as a word of its own (`sed -i '' ...`), GNU sed never does.
    Where that word could be either, the BSD reading is taken, and the word the GNU reading
    would edit is added back when it names an existing file: in-place edits never create one.
    perl, ruby and BSD sed read no option after the first operand (every later word is a
    file); GNU sed reads options anywhere: for sed both readings' files count."""
    is_sed = prog in ("sed", "gsed")
    script_letters = "ef" if is_sed else ("eE" if prog == "perl" else "e")
    inplace = script_given = script_before = False
    suffix_word = first = None
    positional, i = [], 0
    while i < len(args):
        word, lit = args[i], literal(args[i])
        i += 1
        if lit == "--":
            positional += args[i:]
            break
        if lit.startswith("--"):
            name, eq, _ = lit[2:].partition("=")
            opt = sed_long(name) if is_sed and name else None
            if opt == "in-place":
                inplace = True
            elif opt in ("expression", "file", "line-length"):
                script_given = script_given or opt != "line-length"
                i += 0 if eq else 1
            continue
        if not lit.startswith("-") or lit == "-":
            if first is None:
                first, script_before = i - 1, script_given
            positional.append(word)
            if not is_sed:
                positional += args[i:]  # perl and ruby read no option past the first operand
                break
            continue
        letters, j = lit[1:], 0
        while j < len(letters):
            c, rest = letters[j], letters[j + 1:]
            if c == "i" or (is_sed and c == "I"):
                inplace = True
                # BSD sed: a suffix of its own may follow a bare -i/-I ('' for no backup).
                if not rest and prog == "sed" and i < len(args):
                    nxt = literal(args[i])
                    if nxt == "" or SUFFIX.match(nxt):
                        suffix_word, i = args[i], i + 1
                break  # the rest of the cluster, if any, is the suffix
            if c in script_letters:
                script_given = True
                i += 0 if rest else 1  # the script is attached, or the next word
                break
            if is_sed and c == "l":
                # GNU -l N takes digits; BSD -l is a flag of its own.
                if not rest and i < len(args) and literal(args[i]).isdigit():
                    i += 1
                break
            if not is_sed and c in VALUE_LETTERS.get(prog, ""):
                break
            j += 1
            if not is_sed and c in DIGIT_LETTERS.get(prog, ""):
                while j < len(letters) and letters[j].isdigit():
                    j += 1
    if not inplace:
        return None
    files = positional if script_given else positional[1:]
    if is_sed and first is not None:
        # The BSD reading: past the first operand every word is a file (`--` there is not one
        # anyone edits), the operand itself too when a script was given before it.
        rest = [w for w in args[first + 1:] if literal(w) != "--"]
        bsd = ([args[first]] if script_before else []) + rest
        files = files + [w for w in bsd if w not in files]
    if suffix_word is not None:
        # The GNU reading edits the suffix word (a script was given) or the first positional.
        other = suffix_word if script_given else (positional[0] if positional else None)
        if other is not None and exists(other):
            files = [other] + files
    return files or ["?"]


def segment_targets(seg, tokens, quoted, cwd=None, can_stat=False):
    """Write targets of one command segment, as words of hidden text. Only with `can_stat`
    (one simple command, so the hook's cwd is the command's) and a word the shell does not
    expand does the hook look a word up on disk; otherwise every word counts as existing."""
    targets = [m.group(1) for m in REDIRECT.finditer(seg)]

    def literal(w):
        return shell_word(w, quoted, {})

    # The program follows prefix assignments and the keywords of a list (`do sed ...`).
    while tokens and (re.match(r"^\w+=", tokens[0]) or literal(tokens[0]) in KEYWORDS):
        tokens = tokens[1:]
    if not tokens:
        return targets

    def exists(w):
        text = literal(w)
        return (not can_stat or bool(EXPANDS.search(text))
                or os.path.exists(os.path.join(cwd or os.getcwd(), text)))

    # A quoted program name is still that program; `(sed` and `{sed` open a group before it.
    prog = os.path.basename(literal(tokens[0]).lstrip("({"))
    args = tokens[1:]
    if prog == "tee":
        targets += [a for a in args if not a.startswith("-")]
    elif INPLACE.match(prog):
        targets += inplace_targets(prog, args, literal, exists) or []
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
    return targets


def bash_write_targets(cmd, cwd=None):
    """Best-effort list of paths a shell command writes to ("?" means unknown).

    A variable in a target is expanded only from a literal value that the command assigns
    once, at top level, before the target, with no compound syntax or variable-setting
    builtin before either. Any other variable stays as written: the strict reading."""
    hidden, quoted = hide_quotes(HEREDOC.sub(lambda m: "<<" + m.group(3), cmd))
    counts = {}
    for m in ASSIGNED.finditer(hidden):
        name = m.group(1) or m.group(2)
        counts[name] = counts.get(name, 0) + 1
    parts = SEPARATOR.split(hidden)
    # Words are looked up on disk only in one simple command: anything more (a list, a loop,
    # a subshell, cd in any spelling) can run the command elsewhere than the hook's cwd.
    can_stat = len(parts) == 1 and not COMPOUND.search(hidden)
    env, straight, targets = {}, True, []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        before = parts[i - 1] if i else ";"
        after = parts[i + 1] if i + 1 < len(parts) else ";"
        if COMPOUND.search(seg):
            env, straight = {}, False
        tokens = split_words(seg)
        found = segment_targets(seg, tokens, quoted, cwd, can_stat)
        targets += [shell_word(t, quoted, env) for t in found]
        words = tokens[1:] if tokens[:1] == ["export"] else tokens
        if (straight and words and before in (";", "\n") and after != "|"
                and all(ASSIGNMENT.match(w) for w in words)):
            for w in words:
                name, value = w.split("=", 1)
                text = literal_value(value, quoted)
                if text is not None and counts.get(name) == 1:
                    env[name] = text
    clean = []
    for t in targets:
        t = t.strip("'\"")
        if t and t != "?":
            t = os.path.normpath(t)
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


# `git push` options that only change how a branch update is sent. Anything else (force,
# delete, tags, mirror, all, prune, an abbreviated long option) is read as more than that.
PUSH_SAFE_LONG = frozenset(("--set-upstream", "--dry-run", "--quiet", "--verbose", "--progress",
                            "--no-progress", "--verify", "--no-verify", "--porcelain", "--atomic",
                            "--no-atomic", "--ipv4", "--ipv6", "--thin", "--no-thin", "--signed",
                            "--no-signed", "--recurse-submodules", "--no-recurse-submodules",
                            "--push-option", "--repo"))
PUSH_LONG_VALUE = ("--push-option", "--repo")
PUSH_SAFE_SHORT = "unqv46o"
# git commands that may run before a push in the same command without moving HEAD or making tags.
SAFE_BEFORE_PUSH = ("add", "commit", "status", "diff", "log", "show", "fetch", "push")
GIT_PUSH = re.compile(r"\bgit(?:\s+-[Cc]\s+\S+|\s+--?[\w.-]+(?:=\S+)?)*\s+push\b")


def push_branches(cmd, cwd):
    """The branches the command's `git push` calls update, or None when one of them may do
    more than update a branch (force, delete, tags, mirror) or the hook cannot tell where it
    pushes. Strict: a push hidden in quotes, a heredoc or a substitution, a variable, an
    option it does not know, or an unknown current branch all read as None."""
    if HEREDOC.search(cmd) or SUBSTITUTION.search(cmd):
        return None
    hidden, quoted = hide_quotes(cmd)
    if re.search(r"\bGIT_\w*=", hidden):
        return None  # another repository, work tree or configuration
    parts = SEPARATOR.split(hidden)
    knows_cwd = not COMPOUND.search(hidden)
    branches, seen = [], 0
    # Whether an earlier part of the command may have moved HEAD or made a tag, so the
    # repository as the hook sees it now is not the one the push will see.
    moved = [False]

    def git_out(*args):
        rc, out = git(cwd, *args)
        return out.decode().strip() if rc == 0 else ""

    def current():
        """Where a push of the current branch goes (its push ref, else its own name)."""
        if not knows_cwd or moved[0] or git_out("config", "--get", "push.default") == "matching":
            return None
        if git_out("config", "--get-regexp", r"^remote\..*\.push$"):
            return None  # configured push refspecs can send the branch anywhere
        head = git_out("symbolic-ref", "-q", "--short", "HEAD")
        if not head:
            return None
        remote = git_out("for-each-ref", "--format=%(push:remoteref)", "refs/heads/" + head)
        return remote[len("refs/heads/"):] if remote.startswith("refs/heads/") else head

    def is_tag(name):
        return not knows_cwd or moved[0] or bool(git_out("show-ref", "--tags", "--", "refs/tags/" + name))

    for seg in parts[0::2]:
        words = [shell_word(t, quoted, {}) for t in split_words(seg)]
        runs = run_positions(words)
        safe_segment = False
        for i, word in enumerate(words):
            if i not in runs or program(word) != "git":
                continue
            if any(ASSIGNMENT.match(w) for w in words[:i]):
                return None  # `VAR=x git push`: an environment the hook does not share
            j = i + 1
            while j < len(words) and words[j].startswith("-"):
                # Another repository, or configuration that can change where a push goes.
                if words[j].split("=", 1)[0] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                    return None
                j += 1
            sub = words[j] if j < len(words) else ""
            safe_segment = sub in SAFE_BEFORE_PUSH
            if sub != "push":
                break
            seen += 1
            args, positional, k = words[j + 1:], [], 0
            while k < len(args):
                a = args[k]
                if EXPANDS.search(a):
                    return None
                if a == "--":
                    positional += args[k + 1:]
                    break
                if a.startswith("--"):
                    name, _, value = a.partition("=")
                    if name not in PUSH_SAFE_LONG or name == "--recurse-submodules" and value != "check":
                        return None  # on-demand and only push the submodules' branches too
                    k += 2 if name in PUSH_LONG_VALUE and "=" not in a else 1
                    continue
                if a.startswith("-") and len(a) > 1:
                    letters = a[1:]
                    value_at = letters.find("o")
                    checked = letters if value_at < 0 else letters[:value_at]
                    if any(c not in PUSH_SAFE_SHORT for c in checked):
                        return None
                    k += 2 if value_at == len(letters) - 1 else 1
                    continue
                positional.append(a)
                k += 1
            refspecs = positional[1:]
            if not refspecs:
                refspecs = ["HEAD"]
            for ref in refspecs:
                if EXPANDS.search(ref) or ref.startswith(("+", "^")):
                    return None
                src, _, dst = ref.partition(":")
                if not src:
                    return None  # `:branch` deletes it
                is_head = src.upper() == "HEAD" or src == "@"
                dst = dst or src
                if dst.upper() == "HEAD" or dst == "@":
                    dst = current()
                if not dst or not is_head and is_tag(src):
                    return None
                if dst.startswith("refs/heads/"):
                    dst = dst[len("refs/heads/"):]
                if dst.startswith("refs/") or is_tag(dst):
                    return None
                branches.append(dst)
            break
        if not safe_segment and words:
            moved[0] = True
    # Every `git push` the shell would run must be one the hook read.
    return branches if seen and seen == len(GIT_PUSH.findall(cmd)) else None


STATE_REF = re.compile(r"approvals\.json|(^|[\s/'\"=])\.sdlc(/|\b)|sdlc\.py")
READERS = frozenset(("cat", "head", "tail", "ls", "wc", "grep", "egrep", "fgrep", "jq", "diff", "cmp",
                     "stat", "shasum", "sha256sum", "md5", "md5sum", "cksum", "cut", "tr", "nl", "echo",
                     "printf", "read", "pwd", "cd", "basename", "dirname", "realpath", "readlink", "test",
                     "[", "true", "false", "sdlc"))
# `add` and `commit` record files as they are: that is how approvals.json gets committed.
GIT_READERS = frozenset(("status", "log", "show", "diff", "rev-parse", "ls-files", "ls-tree", "ls-remote",
                         "cat-file", "blame", "grep", "describe", "shortlog", "add", "commit"))
FIND_ACTIONS = ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls")
KEYWORDS = frozenset(("if", "then", "else", "elif", "fi", "while", "until", "do", "done", "!", "{", "}",
                      "time"))
OPERATORS = "();<>|&\n"
EXPANSION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def git_reads(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-c" or args[i].startswith("--config-env"):
            return False
        i += 2 if args[i] == "-C" else 1
    return (i < len(args) and args[i] in GIT_READERS and not any(
        a.startswith(("--output", "-O", "--open-files-in-pager", "--upload-pack", "--exec")) for a in args))


def sed_reads(args):
    """sed without in-place editing, script files or the w/W/e commands that write or run."""
    scripts, expect = [], False
    for a in args:
        if expect:
            scripts.append(a)
            expect = False
        elif a.startswith("--"):
            if a.startswith(("--in-place", "--file")):
                return False
            if a.startswith("--expression"):
                scripts += a.split("=", 1)[1:]
                expect = "=" not in a
        elif a.startswith("-") and len(a) > 1:
            flags = a[1:]
            expect = flags.endswith("e")
            if set(flags.rstrip("e")) - set("nErsuz") or flags.count("e") > 1:
                return False
        elif not scripts:
            scripts.append(a)
    return bool(scripts) and not any(re.search(r"[wWe]", s) for s in scripts)


def segment_reads(words):
    while words and (words[0] in KEYWORDS or re.match(r"^\w+=", words[0])):
        words = words[1:]
    if not words or words[0] == "for":  # a loop header runs nothing but its expansions
        return True
    name, args = os.path.basename(words[0]), words[1:]
    if name == "git":
        return git_reads(args)
    if name == "sed":
        return sed_reads(args)
    if name == "find":
        return not any(a in FIND_ACTIONS for a in args)
    return name in READERS


def expansions_read(word):
    while True:
        m = EXPANSION.search(word)
        if not m:
            return "$(" not in word and "`" not in word
        if not reads_only(m.group(1) if m.group(1) is not None else m.group(2)):
            return False
        word = word[:m.start()] + word[m.end():]


def reads_only(cmd):
    """True when every program in cmd only reads (READERS, git log/show/add/commit..., find
    without actions, sed without -i/w/e), $(...) included, and redirects go only to /dev/* or tmp.
    Quoted heredoc bodies are data; an unquoted one expands, so its lines count as commands."""
    text = HEREDOC.sub(lambda m: "<<" + m.group(3) if m.group(1) else m.group(0), cmd)
    lex = shlex.shlex(text, posix=True, punctuation_chars=OPERATORS)
    lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
    try:
        tokens = list(lex)
    except ValueError:
        return False
    segments, words, redirect = [], [], None
    for tok in tokens:
        if tok and all(c in OPERATORS for c in tok):
            is_redirect = tok == ">|" or not any(c in "();|\n" for c in tok) and any(c in "<>" for c in tok)
            redirect = tok if is_redirect else None
            if is_redirect and words and words[-1].isdigit():
                words.pop()  # the fd number of `2>/dev/null`, not an argument
            elif not is_redirect:
                segments.append(words)
                words = []
            continue
        if not expansions_read(tok):
            return False
        if redirect is None:
            words.append(tok)
        elif ">" in redirect and not (tok.isdigit() or tok in SAFE_SINKS
                                      or os.path.normpath(tok).startswith(TMP_PREFIXES)):
            return False
        redirect = None
    segments.append(words)
    return all(segment_reads(w) for w in segments)


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


def base_ref(p):
    """Where the feature branch left the default branch (merge-base), or HEAD when there is
    no default branch to compare with. Commits on the branch do not move it."""
    rc, out = git(p.root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    refs = [out.decode().strip()] if rc == 0 and out.strip() else []
    for ref in refs + ["origin/main", "origin/master", "main", "master"]:
        rc, out = git(p.root, "merge-base", "HEAD", ref)
        if rc == 0 and out.strip():
            return out.decode().strip()
    return "HEAD"


def fingerprint(p, base="HEAD"):
    """Hash of code files that differ from `base` in the work tree; None when not a git repo."""
    rc, _ = git(p.root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return None
    names = set()
    rc, out = git(p.root, "diff", "--name-only", "-z", base)
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
    command = USER_CMD.match(first_line[0]) if first_line else None
    if command and command.group(1).lower() == "status":
        # Nothing for the model to do: show the status without a turn. As the first message
        # of a new desktop session it also starts the session, and with it the SDLC bar.
        emit({"decision": "block", "reason": p.status(sid, verbose=True)})
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
        note = off_plan_note(p, rel, sid)
        if note:
            pre_context(note)
        sys.exit(0)
    p.audit("gate-" + decision, tool=data.get("tool_name"), path=rel, reason=reason)
    pre_decision(decision, reason)


PLAN_FILES = re.compile(r"^##\s+Files affected[ \t]*$(.*?)(?=^##\s|\Z)", re.M | re.S)
PATH_WORD = re.compile(r"^[\w.\-/*?]+$")
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+\.)\s+")


def planned_files(p, slug):
    """Paths, directories and globs under *Files affected* in plan.md, or None when it names none."""
    try:
        with open(os.path.join(p.fdir(slug), "plan.md"), encoding="utf-8") as f:
            section = PLAN_FILES.search(f.read())
    except OSError:
        return None
    entries = []
    for line in (section.group(1) if section else "").splitlines():
        line = line.strip()
        if line.startswith("|"):
            cell = line.strip("|").split("|")[0].strip()  # the table's first column
        elif LIST_ITEM.match(line):
            cell = LIST_ITEM.sub("", line)
        else:
            continue
        for word in re.findall(r"`([^`]+)`", cell) or re.split(r"[\s,]+", cell):
            word = word.strip("()[]:;'\"")
            word = word[2:] if word.startswith("./") else word
            if PATH_WORD.match(word) and ("/" in word or "." in word):
                entries.append(word)
    return entries or None


def in_plan(rel, entries):
    for entry in entries:
        if "*" in entry or "?" in entry:
            if glob_to_regex(entry).match(rel):
                return True
        elif rel == entry.rstrip("/") or rel.startswith(entry.rstrip("/") + "/"):
            return True
    return False


def off_plan_note(p, rel, sid):
    """A note, once per file and session, when the build edits a code or test file that the
    approved plan does not list. It informs; it does not block."""
    if p.classify(rel) not in ("code", "test"):
        return None
    sess = p.session(sid)
    slug = p.active()
    if sess.get("fasttrack") or not slug or p.current_stage(slug)[0] != "build":
        return None
    entries = planned_files(p, slug)
    if not entries or in_plan(rel, entries) or rel in sess.get("off_plan", []):
        return None
    sess["off_plan"] = sess.get("off_plan", []) + [rel]
    p.save_session(sid, sess)
    p.audit("off-plan", feature=slug, path=rel)
    return ("[SDLC] %s is not in *Files affected* of the approved plan.md of `%s`. The edit goes "
            "ahead, but the diff and the plan now differ. If the file belongs to the change, add it "
            "to plan.md and ask the user to re-approve (`sdlc approve plan`); otherwise undo the edit."
            % (rel, slug))


def hook_pre_bash(data, p):
    cmd = (data.get("tool_input") or {}).get("command") or ""
    sid = data.get("session_id")
    if STATE_REF.search(cmd) and not reads_only(cmd):
        p.audit("gate-deny", tool="Bash", command=cmd[:300], reason="sdlc state")
        pre_decision("deny", "SDLC state (.sdlc/, approvals.json) is managed by hooks: shell commands "
                             "may only read it (cat, grep, jq, sed -n, git log/show) and must not run "
                             "sdlc.py. Use the `sdlc` CLI (status, new, lock-tests) or ask the user.")
    if NESTED_USER_CMD.search(cmd) and gated_pattern(cmd, CLAUDE_CLI):
        p.audit("gate-deny", tool="Bash", command=cmd[:300], reason="nested user command")
        pre_decision("deny", "User-only `sdlc` commands count only when the user types them in this "
                             "chat; sending one through another Claude session is blocked. Ask the user.")
    cwd = data.get("cwd") or p.root
    pattern = gated_pattern(cmd, p.cfg["gated_commands"])
    if pattern and re.search(pattern, "git push"):
        # A push that only updates unprotected branches ships nothing: the PR and branch
        # protection guard what reaches the protected ones. The other patterns still apply.
        branches = push_branches(cmd, cwd)
        if branches and not any(matches(b, p.cfg["protected_branches"]) for b in branches):
            pattern = gated_pattern(cmd, [g for g in p.cfg["gated_commands"] if not re.search(g, "git push")])
    if pattern:
        decision = p.cfg["gated_command_decision"]
        p.audit("gate-" + decision, tool="Bash", command=cmd[:300], reason=pattern)
        pre_decision(decision, "SDLC deploy gate: `%s` needs explicit user approval. Make sure "
                               "verification passed and the PR review (skill sdlc:review) is done." % cmd[:120])
    scratch = data.get("scratchpad_dir")
    for target in bash_write_targets(cmd, cwd):
        if target == "?":
            reason = code_gate(p, sid)
            if reason:
                pre_decision("deny", "A shell write whose target the hook cannot read (a patch, an "
                                     "in-place edit with no file) is blocked. " + reason)
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


REVIEWER = re.compile(r"(?:^|:)sdlc-reviewer$")
VERDICT = re.compile(r"READY FOR HUMAN REVIEW|CHANGES REQUIRED")
PR_CREATE = re.compile(r"\b(?:gh\s+pr|glab\s+mr)\s+create\b")
PR_TOOL = re.compile(r"^mcp__.*(?:create|save|open)_?(?:pull|merge)_?request", re.I)
PR_URL = re.compile(r"https?://[^\s\"'<>()\[\]\\]+/(?:pull|merge_requests)/\d+")


def record_review(p, data, text):
    """Keep the sdlc-reviewer's verdict with the change it read, for the SDLC bar."""
    slug = p.active()
    if not slug or not REVIEWER.search(data.get("agent_type") or ""):
        return
    lines = [line for line in (text or "").splitlines() if VERDICT.search(line)]
    if not lines:
        return
    # The last verdict line decides; a line that names both reads as changes required.
    verdict = "changes" if "CHANGES REQUIRED" in lines[-1] else "ready"
    local = p.feature_state(slug)
    local["review"] = {"verdict": verdict, "at": now(), "change": fingerprint(p, base_ref(p))}
    p.save_feature_state(slug, local)
    p.audit("review", feature=slug, verdict=verdict, session=data.get("session_id"))


def record_pr(p, data):
    """Keep the URL of a PR (or MR) the agent opened for the active feature."""
    slug = p.active()
    tool = data.get("tool_name") or ""
    command = (data.get("tool_input") or {}).get("command") or ""
    if not slug or not (PR_TOOL.search(tool) or (tool == "Bash" and PR_CREATE.search(command))):
        return
    m = PR_URL.search(json.dumps(data.get("tool_response"), ensure_ascii=False))
    if not m:
        return
    local = p.feature_state(slug)
    local["pr"] = {"url": m.group(0), "at": now()}
    p.save_feature_state(slug, local)
    p.audit("pr", feature=slug, url=m.group(0))


def plan_mode_context(p, sid):
    """What a plan accepted in plan mode means for the SDLC stage, or None."""
    if p.session(sid).get("fasttrack"):
        return None
    slug = p.active()
    if not slug:
        return ("[SDLC] Accepting a plan in plan mode does not open the code gate. Start a feature "
                "with the sdlc:intent skill, or ask the user to type `sdlc trivial` for a trivial change.")
    stage, _ = p.current_stage(slug)
    if stage == "plan":
        return ("[SDLC] The user accepted this plan in plan mode. That is not the SDLC plan approval: "
                "the code gate stays closed. Write the plan to %s in the sections of the plan "
                "template (skill sdlc:plan), then ask the user to review it and type `sdlc approve "
                "plan`. Do not edit code before that." % p.rel(os.path.join(p.fdir(slug), "plan.md")))
    if stage == "build":
        return ("[SDLC] plan.md of `%s` is already approved. If the plan you presented changes its "
                "files, steps or approach, update plan.md and ask the user to re-approve it before "
                "you edit code." % slug)
    return ("[SDLC] Feature `%s` is at stage %s: the code gate stays closed until intent, spec and "
            "plan are approved. Continue with skill %s." % (slug, STAGE_LABEL[stage], STAGE_SKILL[stage]))


def hook_post_tool(data, p):
    tool = data.get("tool_name") or ""
    if tool == "SubagentHandback":  # auto mode: the subagent's report arrives here
        record_review(p, data, (data.get("tool_input") or {}).get("message"))
    elif tool == "ExitPlanMode":
        context = plan_mode_context(p, data.get("session_id"))
        if context:
            emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}})
    else:
        record_pr(p, data)
    sys.exit(0)


def hook_subagent_stop(data, p):
    record_review(p, data, data.get("last_assistant_message"))
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
        if "last_fail_fp" not in sess:
            sys.exit(0)
        # The last run failed and the fix is already committed (or reverted): check again, or
        # the old failure stays the session's last result. A clean tree is keyed by its commit
        # so the retry limit counts per commit.
        if fp == "clean":
            rc, head = git(p.root, "rev-parse", "HEAD")
            fp = "clean@" + head.decode().strip() if rc == 0 else fp
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
    verb = argv[0] if argv else "status"
    if verb == "state":
        sid = argv[argv.index("--session") + 1] if "--session" in argv[:-1] else None
        print(json.dumps(Project(root).state_dict(sid) if root else {"enabled": False},
                         ensure_ascii=False))
        return 0
    if not root:
        print("SDLC is not enabled here (no %s). Only the user can enable it, by running "
              "`/sdlc:init`." % CONFIG_NAME)
        return 1
    p = Project(root)

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
    print("Usage: sdlc <status|state [--session ID]|features|new <slug> [type]|lock-tests>")
    return 2


GATING_HOOKS = ("pre-edit", "pre-bash")


def hook_failed(p, name, exc):
    """A bug in a hook. The gates fail closed unless the config says "on_hook_error": "allow";
    the other hooks are skipped. Either way the person sees it and audit.log keeps it."""
    error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    sys.stderr.write("sdlc hook error: %r\n" % exc)
    try:
        p.audit("hook-error", hook=name, error=error)
    except Exception:
        pass
    note = "SDLC hook `%s` failed (%s)." % (name, error)
    if name in GATING_HOOKS and p.cfg.get("on_hook_error") != "allow":
        emit({"systemMessage": note + " The gate failed closed and blocked this call. To let calls "
                                      "through until it is fixed, set \"on_hook_error\": \"allow\" in "
                                      "sdlc.config.json.",
              "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": note + " The SDLC gate failed closed, "
                                     "so this call is blocked. Tell the user; do not work around the gate."}})
    emit({"systemMessage": note + " It was skipped for this call."})


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "hook":
        data, p = load_hook()
        name = sys.argv[2]
        handler = {"prompt": hook_prompt, "pre-edit": hook_pre_edit, "pre-bash": hook_pre_bash,
                   "post-edit": hook_post_edit, "post-tool": hook_post_tool,
                   "subagent-stop": hook_subagent_stop, "stop": hook_stop}.get(name)
        if handler:
            try:
                handler(data, p)
            except SystemExit:
                raise
            except Exception as exc:
                hook_failed(p, name, exc)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "cli":
        sys.exit(cli(sys.argv[2:]))
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
