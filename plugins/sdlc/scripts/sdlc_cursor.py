#!/usr/bin/env python3
"""Cursor hook adapter for the SDLC gatekeeper (scripts/sdlc.py).

Hooks (stdin = Cursor hook JSON):  sdlc_cursor.py hook <event>
Events: sessionStart, beforeSubmitPrompt, preToolUse, beforeShellExecution, postToolUse, stop.

The adapter holds no SDLC policy. It turns a Cursor payload into the input of a sdlc.py
handler, runs the handler in this process and maps its Claude Code output to Cursor output.
Like sdlc.py it is inert in a repository without sdlc.config.json.
"""
import io
import json
import os
import re
import sys

CONFIG_NAME = "sdlc.config.json"
GATING = ("preToolUse", "beforeShellExecution")
ALLOW = {"permission": "allow"}
# Tools that only read. Any other tool that names a file, or carries a patch, is checked like
# an edit, so an edit tool that Cursor reports under another name is not waved through. MCP
# tools pass, as they do in Claude Code.
READ_ONLY_TOOLS = frozenset(("Read", "Grep", "Glob", "LS", "Task", "WebFetch", "WebSearch",
                             "SemanticSearch", "ReadLints"))
PATH_FIELDS = ("file_path", "path", "target_file", "notebook_path", "filePath")
LIST_FIELDS = ("paths", "file_paths", "files", "target_files")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): *(.+?)\s*$", re.M)
PATCH_START = "*** Begin Patch"
DIFF_FILE = re.compile(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", re.M)  # unified diff headers
DIFF_HUNK = re.compile(r"^@@ ", re.M)
# A `cd` the shell runs: Cursor's shell keeps its directory for later calls.
CD = re.compile(r"(?:^|[;&|(\n]\s*)cd\s+(?:--\s+)?([^\s;&|()]+)")
# Cursor ignores "ask" for tool calls, and its CLI runs a shell "ask" without a prompt under
# --force, so a write that needs a person is a denial. Only the deploy gate keeps "ask".
MANUAL_STEP = (" Cursor cannot ask for confirmation here, so the call is blocked: ask the user "
               "to make this change themselves.")
RANK = {"allow": 0, "ask": 1, "deny": 2}
MAX_SHELL_DIRS = 6


def neutral(event):
    """The answer that changes nothing. A permission hook must still answer."""
    if event in GATING:
        return dict(ALLOW)
    if event == "beforeSubmitPrompt":
        return {"continue": True}
    return {}


def config_root(start):
    """The nearest directory at or above `start` that holds sdlc.config.json."""
    cur = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(cur, CONFIG_NAME)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def project_root(data, paths=()):
    """The policy root. The workspace roots and project directory that Cursor sets come first:
    the agent controls the edited file's directory and the shell's cwd, and a sdlc.config.json
    planted there must not become the policy. Those count only when the host names no enabled
    root (a workspace opened above the repository)."""
    roots = data.get("workspace_roots")
    host = (roots if isinstance(roots, list) else []) + [os.environ.get("CURSOR_PROJECT_DIR"),
                                                        os.environ.get("CLAUDE_PROJECT_DIR")]
    found = []
    for start in host:
        root = config_root(start) if isinstance(start, str) and start else None
        if root and root not in found:
            found.append(root)
    files = [os.path.realpath(f) for f in paths if isinstance(f, str) and os.path.isabs(f)]
    for root in found:
        real = os.path.realpath(root)
        if any(f == real or f.startswith(real + os.sep) for f in files):
            return root
    if found:
        return found[0]
    fallback = [f if os.path.isdir(f) else os.path.dirname(f) for f in files] + [data.get("cwd")]
    return next((r for r in (config_root(s) for s in fallback if isinstance(s, str) and s) if r), None)


def load_policy():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import sdlc
    return sdlc


class PolicyMissing(Exception):
    pass


def run(handler, data, p):
    """Run a sdlc.py handler and return its Claude Code output ({} when it printed nothing).
    Handlers print one JSON object and exit; exceptions propagate to the caller."""
    out, saved = io.StringIO(), sys.stdout
    sys.stdout = out
    try:
        handler(data, p)
    except SystemExit:
        pass
    finally:
        sys.stdout = saved
    text = out.getvalue().strip()
    return json.loads(text) if text else {}


def decision_of(out):
    spec = out.get("hookSpecificOutput") or {}
    return (spec.get("permissionDecision") or "allow", spec.get("permissionDecisionReason") or "",
            spec.get("additionalContext"))


def answer(decision, reason):
    return {"permission": decision, "user_message": reason, "agent_message": reason}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def edit_targets(data):
    """Files a tool call writes: their paths, ["?"] when it writes something the hook cannot
    name (a Write without a path, a patch without file headers), or [] when it writes nothing."""
    tool = data.get("tool_name") if isinstance(data.get("tool_name"), str) else ""
    if tool == "Shell" or tool.startswith("MCP:") or tool in READ_ONLY_TOOLS:
        return []
    tool_input = data.get("tool_input")
    found = []
    if isinstance(tool_input, dict):
        found += [tool_input[f] for f in PATH_FIELDS if isinstance(tool_input.get(f), str)]
        for f in LIST_FIELDS:
            if isinstance(tool_input.get(f), list):
                found += [x for x in tool_input[f] if isinstance(x, str)]
    texts = list(strings(tool_input))
    if not found and tool not in ("Write", "Delete"):
        # No path field: the files a patch or diff names. A Write's content is only data.
        for text in texts:
            found += PATCH_FILE.findall(text)
            found += [f for f in DIFF_FILE.findall(text) if f != "/dev/null"]
    out = []
    for path in found:
        if path and path not in out:
            out.append(path)
    if out:
        return out
    if tool in ("Write", "Delete") or (not found and any(PATCH_START in t or DIFF_HUNK.search(t) for t in texts)):
        return ["?"]
    return []


def shell_dirs(p, sid):
    dirs = p.session(sid).get("shell_dirs")
    return [d for d in dirs if isinstance(d, str)] if isinstance(dirs, list) else []


def save_shell_dirs(p, sid, dirs):
    sess = p.session(sid)
    if sess.get("shell_dirs") != dirs:
        sess["shell_dirs"] = dirs
        p.save_session(sid, sess)


def cd_targets(command, bases):
    """Directories the command's literal `cd` calls lead to, from each possible start."""
    out = []
    for word in CD.findall(command):
        word = word.strip("'\"")
        if not word or word == "-" or re.search(r"[$`*?\[]", word):
            continue  # a variable, a substitution or a glob: not a directory the hook can name
        word = os.path.expanduser(word)
        for base in bases:
            target = os.path.normpath(word if os.path.isabs(word) else os.path.join(base, word))
            if target not in out:
                out.append(target)
    return out


# ------------------------------------------------------------------------------ events

def on_session_start(sdlc, p, data, sid):
    sess = p.session(sid)
    if "baseline_fp" not in sess:
        sess["baseline_fp"] = sdlc.fingerprint(p)
        p.save_session(sid, sess)
    return {"additional_context": p.status(sid)}


def on_prompt(sdlc, p, data, sid):
    out = run(sdlc.hook_prompt, {"session_id": sid, "cwd": p.root, "prompt": data.get("prompt") or ""}, p)
    if out.get("decision") == "block":  # `sdlc status`: show it, no model turn
        return {"continue": False, "user_message": out.get("reason") or ""}
    _, _, context = decision_of(out)
    return {"continue": True, "additional_context": context} if context else {"continue": True}


def on_pre_tool(sdlc, p, data, sid):
    if data.get("tool_name") == "Shell":
        tool_input = data.get("tool_input")
        cwd = (tool_input.get("cwd") if isinstance(tool_input, dict) else None) or data.get("cwd")
        if isinstance(cwd, str) and cwd:
            save_shell_dirs(p, sid, [cwd])  # the shell is exactly there now
        return dict(ALLOW)
    best, notes = ("allow", ""), []
    for path in edit_targets(data):
        if path == "?":
            reason = sdlc.code_gate(p, sid)
            decision = "deny" if reason else "allow"
            reason = ("A tool call whose target file the hook cannot read is blocked. " + reason) if reason else ""
        else:
            out = run(sdlc.hook_pre_edit, {"session_id": sid, "cwd": p.root, "tool_name": data.get("tool_name"),
                                           "tool_input": {"file_path": path}}, p)
            decision, reason, note = decision_of(out)
            if note:
                notes.append(note)
        if RANK.get(decision, 2) > RANK[best[0]]:
            best = (decision, reason)
    if best[0] != "allow":
        return answer("deny", best[1] + (MANUAL_STEP if best[0] == "ask" else ""))
    if notes:
        # A pre-tool allow carries no context in Cursor: the notes go out after the edit.
        sess = p.session(sid)
        sess["pending_notes"] = sess.get("pending_notes", []) + notes
        p.save_session(sid, sess)
    return dict(ALLOW)


def on_shell(sdlc, p, data, sid):
    command = data.get("command")
    if not isinstance(command, str):
        raise ValueError("beforeShellExecution without a command")
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd:
        known = [cwd]
    else:
        # An empty cwd: the shell is in the root or where an earlier call left it. Judge the
        # command from each; the strictest decision wins.
        known = [p.root] + [d for d in shell_dirs(p, sid) if d != p.root]
    moved = cd_targets(command, known)
    best = ("allow", "")
    for candidate in known + [d for d in moved if d not in known]:
        out = run(sdlc.hook_pre_bash, {"session_id": sid, "cwd": candidate, "tool_name": "Bash",
                                       "tool_input": {"command": command}}, p)
        decision, reason, _ = decision_of(out)
        if decision == "ask" and not reason.startswith(sdlc.DEPLOY_GATE):
            # A write that needs a person: map it before ranking, so a deploy-only ask from
            # another directory cannot hide it.
            decision, reason = "deny", reason + MANUAL_STEP
        if RANK.get(decision, 2) > RANK[best[0]]:
            best = (decision, reason)
    later = (known if cwd else known[1:]) + [d for d in moved if d not in known]
    save_shell_dirs(p, sid, later[-MAX_SHELL_DIRS:])
    return answer(*best) if best[0] != "allow" else dict(ALLOW)


def on_post_tool(sdlc, p, data, sid):
    targets = edit_targets(data)
    if not targets:
        return {}
    for path in targets:
        if path == "?":
            sess = p.session(sid)
            sess["dirty"] = True
            p.save_session(sid, sess)
            continue
        run(sdlc.hook_post_edit, {"session_id": sid, "cwd": p.root, "tool_name": data.get("tool_name"),
                                  "tool_input": {"file_path": path}}, p)
    sess = p.session(sid)
    notes = sess.pop("pending_notes", None)
    if not notes:
        return {}
    p.save_session(sid, sess)
    return {"additional_context": "\n".join(notes)}


def on_stop(sdlc, p, data, sid):
    if data.get("status") != "completed":
        return {}
    out = run(sdlc.hook_stop, {"session_id": sid, "cwd": p.root,
                               "stop_hook_active": (data.get("loop_count") or 0) > 0}, p)
    if out.get("decision") != "block":
        return {}
    # The IDE submits the follow-up through the prompt hook like a typed message: it must
    # never read as a user command.
    reason = re.sub(r"^\s*sdlc\b\s*", "", out.get("reason") or "", flags=re.I)
    return {"followup_message": "[SDLC] " + reason}


HANDLERS = {"sessionStart": on_session_start, "beforeSubmitPrompt": on_prompt,
            "preToolUse": on_pre_tool, "beforeShellExecution": on_shell,
            "postToolUse": on_post_tool, "stop": on_stop}


# ------------------------------------------------------------------------------ main

def handle(event, raw):
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    if event in ("preToolUse", "postToolUse"):
        paths = edit_targets(data)
    else:  # a shell call: its directory picks the root in a multi-root workspace
        paths = [data.get("cwd")] if event == "beforeShellExecution" else []
    root = project_root(data, paths)
    if not root or event not in HANDLERS:
        return neutral(event)
    try:
        sdlc = load_policy()
    except Exception as exc:
        raise PolicyMissing("%s: %s" % (type(exc).__name__, exc))
    p = sdlc.Project(root)
    p.host = "cursor"
    return HANDLERS[event](sdlc, p, data, data.get("conversation_id") or data.get("session_id"))


def failed(event, raw, exc):
    """A bug in the adapter or the policy. The gates fail closed unless the config says
    "on_hook_error": "allow"; the other hooks are skipped. The person sees why."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    root = project_root(data if isinstance(data, dict) else {})
    if not root:
        return neutral(event)
    error = str(exc)[:200] if isinstance(exc, PolicyMissing) else "%s: %s" % (type(exc).__name__, str(exc)[:200])
    sys.stderr.write("sdlc cursor hook error: %s\n" % error)
    try:
        sdlc = load_policy()
        p = sdlc.Project(root)
        p.host = "cursor"
        p.audit("hook-error", hook=event, error=error)
    except Exception:
        pass
    try:
        with open(os.path.join(root, CONFIG_NAME), encoding="utf-8") as f:
            on_error = json.load(f).get("on_hook_error", "deny")
    except (OSError, ValueError, AttributeError):
        on_error = "deny"
    if event not in GATING or on_error == "allow":
        return neutral(event)
    what = "could not load sdlc.py" if isinstance(exc, PolicyMissing) else "failed"
    note = ("SDLC hook `%s` %s (%s). The gate failed closed and blocked this call. To let calls "
            "through until it is fixed, the user sets \"on_hook_error\": \"allow\" in %s or disables "
            "the plugin. Tell the user; do not work around the gate." % (event, what, error, CONFIG_NAME))
    return answer("deny", note)


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "hook":
        print(__doc__)
        sys.exit(2)
    event, raw = sys.argv[2], sys.stdin.read()
    try:
        out = handle(event, raw)
    except Exception as exc:
        out = failed(event, raw, exc)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
