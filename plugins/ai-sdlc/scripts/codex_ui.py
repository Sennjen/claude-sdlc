"""Read-only status snapshots and guarded human actions for the Codex chat UI."""
import argparse
import hashlib
import html
import json
import os
import re
import shlex

import sdlc


# Keep transport prompts in English and localize only visible interface text.
LABELS = {
    "uk": {
        "Not enabled": "Не увімкнено", "No active feature": "Немає активної задачі",
        "Initialize the workflow in this repository.": "Увімкніть SDLC у цьому репозиторії.",
        "Initialize SDLC": "Увімкнути SDLC", "Refresh SDLC": "Оновити стан",
        "Run verification": "Запустити перевірку", "Start feature": "Створити задачу",
        "Close feature": "Закрити задачу", "Continue building": "Продовжити реалізацію",
        "Create PR": "Створити PR", "Review changes": "Перевірити зміни",
        "Unlock tests": "Розблокувати тести", "End fast-track": "Завершити fast-track",
        "Approve": "Погодити", "Write": "Підготувати", "Switch to": "Перейти до",
        "approved": "погоджено", "draft": "чернетка", "stale": "застаріло",
        "missing": "немає", "skipped": "пропущено", "ready": "готово",
        "open": "дозволено", "closed": "заборонено", "locked": "заблоковано",
        "unlocked": "розблоковано", "passed": "успішно", "failed": "помилка",
        "not run": "не запускалася", "not configured": "не налаштовано",
        "previous change": "попередня зміна", "Code changes": "Зміни коду",
        "Tests": "Тести", "Verification": "Перевірка", "Review": "Рев’ю",
        "Security": "Безпека", "changes": "потрібні зміни",
        "Plan progress": "Прогрес плану", "steps": "кроків", "Actions": "Дії",
        "Pull request": "Pull request", "Fast-track is active.": "Fast-track увімкнено.",
        "Session context is unavailable. Refresh through trusted Codex hooks to enable actions.":
            "Немає контексту сесії. Оновіть стан через довірені hooks Codex, щоб увімкнути дії.",
        "Snapshot. Refresh to see changes.": "Знімок стану. Оновіть, щоб побачити зміни.",
        "This client cannot send actions from this card. Type this command in the chat:":
            "Цей клієнт не може надіслати дію з картки. Введіть цю команду в чаті:",
        "Confirm the request in Codex.": "Підтвердьте запит у Codex.",
        "Check the request in the chat. This card remains a snapshot.":
            "Перевірте запит у чаті. Ця картка залишається знімком стану.",
        "The request was not confirmed. Retry or type this command in the chat:":
            "Запит не підтверджено. Повторіть або введіть цю команду в чаті:",
    },
    "ru": {
        "Not enabled": "Не включён", "No active feature": "Нет активной задачи",
        "Initialize the workflow in this repository.": "Включите SDLC в этом репозитории.",
        "Initialize SDLC": "Включить SDLC", "Refresh SDLC": "Обновить статус",
        "Run verification": "Запустить проверку", "Start feature": "Создать задачу",
        "Close feature": "Закрыть задачу", "Continue building": "Продолжить реализацию",
        "Create PR": "Создать PR", "Review changes": "Проверить изменения",
        "Unlock tests": "Разблокировать тесты", "End fast-track": "Завершить fast-track",
        "Approve": "Утвердить", "Write": "Подготовить", "Switch to": "Перейти к",
        "approved": "утверждено", "draft": "черновик", "stale": "устарело",
        "missing": "нет", "skipped": "пропущено", "ready": "готово",
        "open": "разрешены", "closed": "запрещены", "locked": "заблокированы",
        "unlocked": "разблокированы", "passed": "успешно", "failed": "ошибка",
        "not run": "не запускалась", "not configured": "не настроена",
        "previous change": "предыдущее изменение", "Code changes": "Изменения кода",
        "Tests": "Тесты", "Verification": "Проверка", "Review": "Ревью",
        "Security": "Безопасность", "changes": "нужны изменения",
        "Plan progress": "Прогресс плана", "steps": "шагов", "Actions": "Действия",
        "Pull request": "Pull request", "Fast-track is active.": "Fast-track включён.",
        "Session context is unavailable. Refresh through trusted Codex hooks to enable actions.":
            "Нет контекста сессии. Обновите статус через доверенные hooks Codex, чтобы включить действия.",
        "Snapshot. Refresh to see changes.": "Снимок состояния. Обновите, чтобы увидеть изменения.",
        "This client cannot send actions from this card. Type this command in the chat:":
            "Этот клиент не может отправить действие из карточки. Введите эту команду в чате:",
        "Confirm the request in Codex.": "Подтвердите запрос в Codex.",
        "Check the request in the chat. This card remains a snapshot.":
            "Проверьте запрос в чате. Эта карточка остаётся снимком состояния.",
        "The request was not confirmed. Retry or type this command in the chat:":
            "Запрос не подтверждён. Повторите или введите эту команду в чате:",
    },
}


def translate(text, language):
    labels = LABELS.get(language, {})
    if text in labels:
        return labels[text]
    for prefix in ("Approve", "Write", "Switch to"):
        if text.startswith(prefix + " "):
            return labels.get(prefix, prefix) + text[len(prefix):]
    return text


def progress(p, slug):
    if not slug:
        return {"done": 0, "total": 0}
    def read(name):
        try:
            with open(os.path.join(p.fdir(slug), name), encoding="utf-8") as source:
                return source.read()
        except OSError:
            return ""
    section = next((part for part in re.split(r"^##\s+", read("plan.md"), flags=re.M)
                    if part.startswith("Work sequence")), "")
    steps = set(re.findall(r"^\s*(\d+)\.\s+\[[ xX]\]", section, re.M))
    done = set(re.findall(r"^\s*-\s*\[[xX]\]\s*Step\s+(\d+)\b", read("progress.md"), re.M | re.I))
    return {"done": len(steps & done), "total": len(steps)}


def view(p, sid):
    """Bind a card to its repository, session, artifacts, code, and test-lock instance."""
    state = p.state_dict(sid)
    state["progress"] = progress(p, state["active"])
    current = sdlc.fingerprint(p)
    change = sdlc.fingerprint(p, sdlc.base_ref(p))
    state["code_changed"] = change not in (None, "clean")
    sess = p.session(sid) if sid else {}
    # A commit keeps the code a pass checked: compare against the commit the pass was taken
    # against. A session from before last_pass_change compares the uncommitted changes.
    if "last_pass_change" in sess:
        base = sess.get("last_pass_base")
        passed = (sdlc.fingerprint(p, base) if base else change) == sess["last_pass_change"]
    else:
        passed = sess.get("last_pass_fp") == current
    state["verify"]["current"] = not state["verify"]["commands"] or bool(
        sid and state["verify"]["last"] and state["verify"]["last"]["ok"] and passed)
    lock = os.path.join(p.state, "tests-locked")
    try:
        stat = os.stat(lock)
        lock_version = [sdlc.sha256_file(lock), stat.st_mtime_ns, stat.st_ino]
    except FileNotFoundError:
        lock_version = None
    # Stop can update the last verification result after the assistant renders a
    # card. Informational timestamps must not expire every button immediately.
    # The resolver separately checks that the selected action is still offered.
    bound = {"root": p.root, "session": sid, "active": state["active"],
             "stages": state["stages"], "features": state["features"],
             "fasttrack": state["fasttrack"], "progress": state["progress"],
             "verify_commands": state["verify"]["commands"],
             "pr": state["pr"]["url"] if state["pr"] else None,
             "lock": lock_version, "change": change}
    token = hashlib.sha256(json.dumps(bound, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return state, token


def review_text(review):
    """A reviewer's verdict on the current code, `stale` after a code change, or `not run`."""
    return "not run" if not review else (review["verdict"] if review["current"] else "stale")


def actions(state, token, sid):
    """Offer only actions valid for this snapshot. No command here executes an action."""
    result = []
    active = state["active"] or "-"
    def add(action, label, command=None, skill=None, target=active):
        result.append({"id": action, "target": target, "label": label,
                       "command": command, "skill": skill,
                       "prompt": "sdlc ui %s %s %s" % (action, target, token)})
    if not sid:
        return result
    stage = state["stage"]
    if state["fasttrack"]:
        add("verify", "Run verification", skill="verify")
    elif not state["active"]:
        add("capture-intent", "Start feature", skill="intent")
    elif stage in sdlc.STAGES:
        artifact = state["stages"][stage]
        if artifact["sha256"]:
            add("approve-" + stage, "Approve " + stage,
                command="sdlc approve %s --feature %s --sha256 %s" %
                        (stage, active, artifact["sha256"]))
        else:
            add("write-" + stage, "Write " + stage, skill=stage)
    elif state["pr"]:
        add("close-feature", "Close feature", command="sdlc done")
    elif (state["progress"]["total"] > state["progress"]["done"]
          or (not state["code_changed"] and state["verify"]["last"] is None)):
        add("build", "Continue building", skill="build")
    elif not state["verify"]["current"]:
        add("verify", "Run verification", skill="verify")
    elif all(review_text(state[kind]) == "ready" for kind in ("review", "security")):
        add("create-pr", "Create PR", skill="review pr")
    else:
        add("review", "Review changes", skill="review")
    if stage == "build" and not any(a["id"] == "verify" for a in result):
        add("verify", "Run verification", skill="verify")
    if state["tests_locked"]:
        add("unlock-tests", "Unlock tests", command="sdlc unlock tests")
    if state["fasttrack"]:
        add("end-fasttrack", "End fast-track", command="sdlc trivial off")
    for feature in [f for f in state["features"] if not f["done"] and f["slug"] != state["active"]][:3]:
        add("switch-feature", "Switch to " + feature["slug"],
            command="sdlc feature " + feature["slug"], target=feature["slug"])
    return result


def followup(label, prompt):
    label = label.replace("]", ")").replace("\n", " ")
    return "- :codex-followup[%s]{prompt=%s}" % (label, json.dumps(prompt, ensure_ascii=False))


def render(p, sid=None):
    """Produce a current card without writing approvals, session state, or audit entries."""
    if p is None:
        return ("**SDLC · Not enabled**\n\nInitialize the workflow in this repository.\n\n" +
                followup("Initialize SDLC", "Use $ai-sdlc:init to enable the SDLC workflow in this repository."))
    state, token = view(p, sid)
    title = "**SDLC · %s**" % (state["active"] or "No active feature")
    lines = [title, ""]
    if state["active"]:
        lines.append(" · ".join("%s: %s" % (stage.title(), state["stages"][stage]["state"])
                                for stage in sdlc.STAGES))
    verify = state["verify"]["last"]
    verification = "not run" if verify is None else ("passed" if verify["ok"] else "failed")
    if not state["verify"]["commands"]:
        verification = "not configured"
    if verify and verify["ok"] and not state["verify"]["current"]:
        verification += " (previous change)"
    lines.append("Code gate: %s · Tests: %s · Verify: %s · Review: %s · Security: %s" %
                 (state["code_gate"], "locked" if state["tests_locked"] else "unlocked",
                  verification, review_text(state["review"]), review_text(state["security"])))
    if state["progress"]["total"]:
        lines.append("Plan progress: {done}/{total} steps".format(**state["progress"]))
    if state["fasttrack"]:
        lines.append("Fast-track is active for this session.")
    links = []
    for stage in sdlc.STAGES:
        artifact = state["stages"].get(stage)
        if artifact and artifact["sha256"]:
            path = os.path.join(p.root, artifact["path"]).replace(">", "%3E")
            links.append("[%s.md](<%s>)" % (stage, path))
    if state["pr"]:
        links.append("[Pull request](%s)" % state["pr"]["url"])
    if links:
        lines.extend(["", " · ".join(links)])
    if not sid:
        lines.extend(["", "Session context is unavailable. Refresh through trusted Codex hooks "
                      "before using action buttons."])
    lines.append("")
    lines.extend(followup(action["label"], action["prompt"]) for action in actions(state, token, sid))
    lines.append(followup("Refresh SDLC", "sdlc ui refresh"))
    return "\n".join(lines)


def render_html(p, sid=None, language="en"):
    """Render an inline fragment; actions only send human-confirmed chat requests."""
    def label(text):
        return html.escape(translate(text, language), quote=True)

    def button(text, prompt, primary=False):
        return ('<button type="button" class="btn%s" data-prompt="%s">%s</button>' %
                (" btn-primary" if primary else "", html.escape(prompt, quote=True), label(text)))

    content = []
    if p is None:
        title = label("Not enabled")
        content.append("<div>%s</div>" % label("Initialize the workflow in this repository."))
        offered = [{"label": "Initialize SDLC", "prompt":
                    "Use $ai-sdlc:init to enable the SDLC workflow in this repository."}]
        refresh = "Use $ai-sdlc:status to show the current SDLC status only."
    else:
        state, token = view(p, sid)
        title = html.escape(state["active"], quote=True) if state["active"] else label("No active feature")
        if state["active"]:
            content.append('<div class="sdlc-stages">%s</div>' % "".join(
                '<span class="viz-badge">%s: %s</span>' %
                (stage.title(), label(state["stages"][stage]["state"])) for stage in sdlc.STAGES))
        done, total = state["progress"]["done"], state["progress"]["total"]
        if total:
            content.append(
                '<div class="sdlc-progress"><div>%s · <span class="tabular-nums">%d/%d</span> %s</div>'
                '<div class="progress" role="progressbar" aria-label="%s" aria-valuenow="%d" '
                'aria-valuemin="0" aria-valuemax="%d"><div class="progress-bar" style="width:%.2f%%">'
                '</div></div></div>' %
                (label("Plan progress"), done, total, label("steps"), label("Plan progress"),
                 done, total, 100 * done / total))
        verify = state["verify"]["last"]
        verification = "not run" if verify is None else ("passed" if verify["ok"] else "failed")
        if not state["verify"]["commands"]:
            verification = "not configured"
        verification = label(verification)
        if verify and verify["ok"] and not state["verify"]["current"]:
            verification += " (%s)" % label("previous change")
        details = [("Code changes", label(state["code_gate"])),
                   ("Tests", label("locked" if state["tests_locked"] else "unlocked")),
                   ("Verification", verification), ("Review", label(review_text(state["review"]))),
                   ("Security", label(review_text(state["security"])))]
        content.append('<dl class="sdlc-details">%s</dl>' % "".join(
            '<div><dt>%s:</dt><dd>%s</dd></div>' % (label(key), value) for key, value in details))
        if state["fasttrack"]:
            content.append("<div>%s</div>" % label("Fast-track is active."))
        documents = []
        for stage in sdlc.STAGES:
            artifact = state["stages"].get(stage)
            if artifact and artifact["sha256"]:
                path = os.path.join(p.root, artifact["path"])
                documents.append(button(stage + ".md",
                    "Open this SDLC document in Codex without modifying it: " + json.dumps(path)))
        # Web links remain ordinary links; file buttons request opening through the
        # chat bridge because sandboxed fragments cannot navigate local file paths.
        if state["pr"] and re.match(r"^https?://", state["pr"]["url"], re.I):
            documents.append('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' %
                             (html.escape(state["pr"]["url"], quote=True), label("Pull request")))
        if documents:
            content.append('<div class="sdlc-files">%s</div>' % "".join(documents))
        if not sid:
            content.append("<div>%s</div>" % label(
                "Session context is unavailable. Refresh through trusted Codex hooks to enable actions."))
        offered = actions(state, token, sid)
        refresh = "sdlc ui refresh"

    controls = [button(action["label"], action["prompt"], primary=index == 0)
                for index, action in enumerate(offered)]
    controls.append(button("Refresh SDLC", refresh))
    replacements = {
        "LANG": html.escape(language, quote=True), "TITLE": title,
        "CONTENT": "\n".join(content), "ACTIONS": "\n".join(controls),
        "ACTIONS_LABEL": label("Actions"), "SNAPSHOT": label("Snapshot. Refresh to see changes."),
        "UNAVAILABLE": label("This client cannot send actions from this card. Type this command in the chat:"),
        "PENDING": label("Confirm the request in Codex."),
        "SENT": label("Check the request in the chat. This card remains a snapshot."),
        "FAILED": label("The request was not confirmed. Retry or type this command in the chat:"),
    }
    with open(os.path.join(sdlc.PLUGIN_ROOT, "assets", "codex-status.html"), encoding="utf-8") as source:
        template = source.read()
    return re.sub(r"@@([A-Z_]+)@@", lambda match: replacements[match.group(1)], template)


def chat(p, argv):
    parser = argparse.ArgumentParser(prog="sdlc chat")
    parser.add_argument("--session")
    parser.add_argument("--html", action="store_true", help="emit an inline interactive fragment")
    parser.add_argument("--language", choices=("en", "uk", "ru"), default="en")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code
    print(render_html(p, args.session, args.language) if args.html else render(p, args.session))
    return 0


def resolve_action(p, sid, line):
    """Resolve a human chat click to a normal user command or a skill request."""
    match = sdlc.USER_CMD.match(line)
    if not match or match.group(1).lower() != "ui":
        return line, None
    args = (match.group(2) or "").split()
    if args == ["refresh"]:
        return "", "[SDLC] Show the refreshed chat status only. Do not start or resume implementation."
    if len(args) != 3 or not sid:
        return "", "[SDLC] Invalid chat action. Refresh the SDLC card; no action was performed."
    action_id, target, shown = args
    state, current = view(p, sid)
    selected = next((a for a in actions(state, current, sid)
                     if a["id"] == action_id and a["target"] == target), None)
    if shown != current or selected is None:
        p.audit("ui-stale", action=action_id, feature=target, session=sid)
        return "", ("[SDLC] This chat button is stale: the feature, document, code, lock, or session "
                    "changed. No action was performed. Show a fresh card and wait for the user "
                    "to select its action; do not approve or execute the old request yourself.")
    p.audit("ui-action", action=action_id, feature=target, session=sid)
    if selected["command"]:
        return selected["command"], None
    skill, _, argument = selected["skill"].partition(" ")
    return "", ("[SDLC] The user selected `%s` for feature `%s`. Use the ai-sdlc:%s skill%s. "
                "Follow its existing gates and authorization requirements." %
                (action_id, target, skill, " with argument `" + argument + "`" if argument else ""))


def prompt_context(p, sid):
    command = "CLAUDE_PROJECT_DIR=%s %s chat" % (
        shlex.quote(p.root), shlex.quote(os.path.join(sdlc.PLUGIN_ROOT, "bin", "sdlc")))
    if sid:
        command += " --session " + shlex.quote(sid)
    return ("[SDLC chat UI] In the primary agent's user-facing final response, append a fresh "
            "interactive SDLC block. Immediately before responding, run this read-only renderer:\n"
            + command + " --html\nAdd --language uk or --language ru to match the user's language. "
            "Redirect its literal HTML output into a new sdlc-status-UNIQUE_ID.html file in this task's "
            "writable visualization directory outside the repository. If none is supplied, use "
            + shlex.quote(os.path.join(p.art, "_ui")) + " as the task-owned output directory. "
            "Do not save it among code files: that would expire its own snapshot token. Read the file "
            "back, then emit its absolute path using this exact content-reference syntax, outside "
            "code fences:\nvisualize{\"path\":\"/absolute/path/sdlc-status.html\"}\n"
            "Do not substitute a Markdown list or :codex-followup directives for the buttons. "
            "Preserve the generated data-prompt values exactly. Never execute button "
            "prompts or click human-only actions yourself. Read the card again after edits. "
            "Do not add cards to subagent reports. The card is a snapshot; earlier messages do "
            "not update in place. If inline visualizations are unavailable in the client, clearly "
            "report that limitation and use the plain sdlc chat output as text; do not claim it "
            "has working buttons.")
