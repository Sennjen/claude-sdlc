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

sys.dont_write_bytecode = True  # never write __pycache__ into the installed plugin

CONFIG_NAME = "sdlc.config.json"
GATING = ("preToolUse", "beforeShellExecution")
ALLOW = {"permission": "allow"}
# Tools that only read. Any other tool whose input names a file is checked like an edit, so an
# edit tool that Cursor reports under another name is not waved through. MCP tools pass, as
# they do in Claude Code.
READ_ONLY_TOOLS = frozenset(("Read", "Grep", "Glob", "LS", "Task", "WebFetch", "WebSearch",
                             "SemanticSearch", "ReadLints"))
PATH_FIELDS = ("file_path", "path", "target_file", "notebook_path", "filePath")
# Cursor ignores "ask" for tool calls, so a file decision that needs a person is a denial.
MANUAL_STEP = (" Cursor cannot ask for confirmation of a file edit, so it is blocked: ask the user "
               "to make this change themselves.")
RANK = {"allow": 0, "ask": 1, "deny": 2}


def neutral(event):
    """The answer that changes nothing. A permission hook must still answer."""
    if event in GATING:
        return dict(ALLOW)
    if event == "beforeSubmitPrompt":
        return {"continue": True}
    return {}


def starts(data, path=None):
    """Where to look for sdlc.config.json: the edited file's directory first, then the
    shell's directory and the workspace roots."""
    out = [os.path.dirname(path)] if isinstance(path, str) and os.path.isabs(path) else []
    roots = data.get("workspace_roots")
    out += [data.get("cwd")] + (roots if isinstance(roots, list) else [])
    out += [os.environ.get("CURSOR_PROJECT_DIR"), os.environ.get("CLAUDE_PROJECT_DIR")]
    return [s for s in out if isinstance(s, str) and s]


def config_root(candidates):
    for start in candidates:
        cur = os.path.abspath(start)
        while True:
            if os.path.isfile(os.path.join(cur, CONFIG_NAME)):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


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
    return spec.get("permissionDecision") or "allow", spec.get("permissionDecisionReason") or "", \
        spec.get("additionalContext")


def answer(decision, reason):
    return {"permission": decision, "user_message": reason, "agent_message": reason}


def edit_path(data):
    """The file a tool call writes, or None when the call writes no file."""
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    if tool in ("Write", "Delete"):
        return tool_input.get("file_path")
    if tool == "Shell" or tool.startswith("MCP:") or tool in READ_ONLY_TOOLS:
        return None
    return next((tool_input[f] for f in PATH_FIELDS if f in tool_input), None)


def remember_shell_cwd(p, sid, cwd):
    """Cursor's shell keeps its directory between calls, but a call reports it only when the
    agent sets it. Keep the last one, to judge relative write targets of later calls."""
    if isinstance(cwd, str) and cwd:
        sess = p.session(sid)
        if sess.get("shell_cwd") != cwd:
            sess["shell_cwd"] = cwd
            p.save_session(sid, sess)


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
        remember_shell_cwd(p, sid, (data.get("tool_input") or {}).get("cwd") or data.get("cwd"))
        return dict(ALLOW)
    path = edit_path(data)
    if path is None:
        return dict(ALLOW)
    out = run(sdlc.hook_pre_edit, {"session_id": sid, "cwd": p.root, "tool_name": data.get("tool_name"),
                                   "tool_input": {"file_path": path}}, p)
    decision, reason, note = decision_of(out)
    if decision in ("deny", "ask"):
        return answer("deny", reason + (MANUAL_STEP if decision == "ask" else ""))
    if note:
        # A pre-tool allow carries no context in Cursor: the note goes out after the edit.
        sess = p.session(sid)
        sess["pending_notes"] = sess.get("pending_notes", []) + [note]
        p.save_session(sid, sess)
    return dict(ALLOW)


def on_shell(sdlc, p, data, sid):
    command = data.get("command")
    if not isinstance(command, str):
        raise ValueError("beforeShellExecution without a command")
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd:
        remember_shell_cwd(p, sid, cwd)
        candidates = [cwd]
    else:
        # An empty cwd: the shell may be in the root or where the agent last put it. Judge
        # the command from both; the strictest decision wins.
        stored = p.session(sid).get("shell_cwd")
        candidates = [p.root] + ([stored] if stored and stored != p.root else [])
    best = ("allow", "")
    for candidate in candidates:
        out = run(sdlc.hook_pre_bash, {"session_id": sid, "cwd": candidate, "tool_name": "Bash",
                                       "tool_input": {"command": command}}, p)
        decision, reason, _ = decision_of(out)
        if RANK.get(decision, 0) > RANK[best[0]]:
            best = (decision, reason)
    return answer(*best) if best[0] != "allow" else dict(ALLOW)


def on_post_tool(sdlc, p, data, sid):
    path = edit_path(data)
    if path is None:
        return {}
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
    path = edit_path(data) if event in ("preToolUse", "postToolUse") else None
    root = config_root(starts(data, path))
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
    root = config_root(starts(data if isinstance(data, dict) else {}))
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
