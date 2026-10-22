#!/usr/bin/env python3
"""Translate Codex lifecycle hooks to the shared SDLC gatekeeper.

Usage: codex.py <prompt|pre-tool|post-tool|subagent-stop|stop>
Input and output follow https://learn.chatgpt.com/docs/hooks.
"""
import json
import os
import re
import sys

import sdlc
import codex_ui


def emit(obj):
    # Codex reports `ask` as a hook error and then runs the tool. Deny instead.
    specific = obj.get("hookSpecificOutput", {})
    if specific.get("permissionDecision") == "ask":
        specific["permissionDecision"] = "deny"
        specific["permissionDecisionReason"] += (
            " Codex hooks cannot request interactive approval. The user must make this edit "
            "or run this command outside the agent; do not retry through another tool.")
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    raise SystemExit(0)


def normalize(data):
    """Accept the canonical hook inputs and raw local tool argument names."""
    data = dict(data)
    ti = data.get("tool_input")
    if isinstance(ti, str):
        ti = {"command": ti}
    elif not isinstance(ti, dict):
        ti = {}
    else:
        ti = dict(ti)
    tool = data.get("tool_name", "")
    if tool in ("exec_command", "shell_command", "shell"):
        data["tool_name"] = "Bash"
    if data.get("tool_name") == "Bash":
        ti.setdefault("command", ti.get("cmd", ""))
        if ti.get("workdir"):
            data["cwd"] = os.path.abspath(os.path.join(data.get("cwd") or os.getcwd(), ti["workdir"]))
    data["tool_input"] = ti
    return data


def patch_paths(command):
    """Read every source and destination in an apply_patch; never infer just one file."""
    if not isinstance(command, str):
        raise ValueError("apply_patch has no string command")
    lines = command.strip().splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("unrecognized apply_patch envelope")
    paths = []
    for line in lines[1:-1]:
        for prefix in ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "):
            if line.startswith(prefix):
                path = line[len(prefix):].strip()
                if not path.strip() or "\x00" in path:
                    raise ValueError("empty or invalid patch path")
                paths.append(path)
                break
        else:
            if line.startswith("*** ") and line != "*** End of File":
                raise ValueError("unrecognized patch header")
    if not paths:
        raise ValueError("apply_patch names no files")
    return list(dict.fromkeys(paths))


def pre_patch(data, p):
    paths = patch_paths(data["tool_input"].get("command"))
    sid = data.get("session_id")
    relatives = [p.rel(path, data.get("cwd")) for path in paths]
    touched = set(rel for rel in relatives if rel is not None)
    active = p.active()
    for rel in touched:
        # All paths are checked against the pre-patch filesystem. Also account for
        # approvals this very patch would invalidate before allowing dependent work.
        if rel.startswith(p.art_rel + "/"):
            parts = rel[len(p.art_rel) + 1:].split("/")
            if len(parts) == 2 and parts[1] in ("spec.md", "plan.md"):
                upstream = ("intent",) if parts[1] == "spec.md" else ("intent", "spec")
                if any(p.rel(os.path.join(p.fdir(parts[0]), stage + ".md")) in touched
                       for stage in upstream):
                    p.audit("gate-deny", tool="apply_patch", path=rel, reason="upstream artifact in same patch")
                    sdlc.pre_decision("deny", "SDLC: this patch changes an upstream artifact and "
                                      "its dependent document. Split the patch and obtain a fresh "
                                      "approval before writing " + parts[1] + ".")
        if p.classify(rel) == "code" and active and not p.session(sid).get("fasttrack"):
            approvals = p.approvals(active)
            changed_approved = [stage for stage in sdlc.STAGES
                                if p.stage_state(active, stage, approvals) == "approved"
                                and p.rel(os.path.join(p.fdir(active), stage + ".md")) in touched]
            if changed_approved:
                p.audit("gate-deny", tool="apply_patch", path=rel, reason="approved artifact and code in same patch")
                sdlc.pre_decision("deny", "SDLC: this patch changes an approved artifact and code. "
                                  "Update the artifact separately and ask the user to re-approve it "
                                  "before changing code.")
    # Check the whole patch before changing active-feature state or emitting context.
    for rel in relatives:
        if rel is None:
            continue
        decision, reason = sdlc.check_target(p, rel, sid)
        if decision != "allow":
            p.audit("gate-" + decision, tool="apply_patch", path=rel, reason=reason)
            sdlc.pre_decision(decision, reason)
    notes = []
    for rel in relatives:
        if rel is None:
            continue
        prefix, suffix = p.art_rel + "/", "/intent.md"
        if rel.startswith(prefix) and rel.endswith(suffix):
            slug = rel[len(prefix):-len(suffix)]
            if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug) and (not p.active() or p.is_done(p.active())):
                p.set_active(slug)
                notes.append("[SDLC] `%s` is now the active feature." % slug)
        note = sdlc.off_plan_note(p, rel, sid)
        if note:
            notes.append(note)
    if notes:
        sdlc.pre_context("\n".join(notes))


def pre_tool(data, p):
    tool = data.get("tool_name")
    if tool == "apply_patch":
        pre_patch(data, p)
    elif tool == "Bash":
        sdlc.hook_pre_bash(data, p)
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        sdlc.hook_pre_edit(data, p)


def post_tool(data, p):
    tool = data.get("tool_name")
    if tool == "apply_patch":
        for path in patch_paths(data["tool_input"].get("command")):
            rel = p.rel(path, data.get("cwd"))
            if rel and p.classify(rel) in ("code", "test", "protected"):
                sid = data.get("session_id")
                sess = p.session(sid)
                sess["dirty"] = True
                p.save_session(sid, sess)
                break
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        sdlc.hook_post_edit(data, p)
    else:
        sdlc.hook_post_tool(data, p)


# Codex does not load Claude's agents/ profiles. The review skill gives each reviewer's
# procedure to a fresh read-only subagent with one of these feature markers, regardless of
# its profile name.
REVIEW_MARKERS = (("SDLC REVIEW: ", "sdlc-reviewer"),
                  ("SDLC SECURITY REVIEW: ", "sdlc-security-reviewer"))


def subagent_stop(data, p):
    text = data.get("last_assistant_message") or ""
    slug = p.active()
    lines = text.strip().splitlines()
    if not slug or not lines:
        return
    for marker, agent in REVIEW_MARKERS:
        if lines[0] == marker + slug:
            sdlc.record_review(p, dict(data, agent_type=agent), text)
            return


def prompt(data, p):
    sid = data.get("session_id")
    first = (data.get("prompt") or "").strip().splitlines()[:1]
    line = first[0] if first else ""
    mapped, message = codex_ui.resolve_action(p, sid, line)
    if mapped != line:
        data = dict(data, prompt=mapped)
    original_emit = sdlc.emit
    def with_card(obj):
        # Codex needs a model reply to embed the interactive block. Claude's original
        # status command still blocks the model turn in the shared hook path.
        if obj.get("decision") == "block":
            obj = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": obj.get("reason", "")}}
        specific = obj.setdefault("hookSpecificOutput", {"hookEventName": "UserPromptSubmit"})
        context = [specific.get("additionalContext", "")]
        if message:
            context.append(message)
        if line.lower().strip() == "sdlc status":
            context.append("[SDLC] Show the status only. Do not start or resume implementation.")
        context.append(codex_ui.prompt_context(p, sid))
        specific["additionalContext"] = "\n".join(context)
        original_emit(obj)
    sdlc.emit = with_card
    try:
        sdlc.hook_prompt(data, p)
    finally:
        sdlc.emit = original_emit


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("prompt", "pre-tool", "post-tool", "subagent-stop", "stop"):
        sys.stderr.write(__doc__ + "\n")
        return 2
    name = sys.argv[1]
    sdlc.emit = emit
    p = None
    try:
        data = json.load(sys.stdin)
        root = sdlc.find_root(data.get("cwd") or os.getcwd())
        if not root:
            return 0
        p = sdlc.Project(root)
        data = normalize(data)
        if name == "prompt":
            prompt(data, p)
        elif name == "pre-tool":
            pre_tool(data, p)
        elif name == "post-tool":
            post_tool(data, p)
        elif name == "subagent-stop":
            subagent_stop(data, p)
        else:
            sdlc.hook_stop(data, p)
    except Exception as exc:
        if p is not None:
            sdlc.hook_failed(p, "pre-edit" if name == "pre-tool" else name, exc)
        elif name == "pre-tool":
            sdlc.pre_decision("deny", "SDLC could not read the Codex hook input: %s" % exc)
        else:
            emit({"systemMessage": "SDLC Codex hook failed: %s" % exc})
    return 0


if __name__ == "__main__":
    sys.exit(main())
