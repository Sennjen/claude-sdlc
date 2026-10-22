"""Exercise chat card actions through actual Codex prompt hooks, without model calls."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_codex

sys.path.insert(0, str(test_codex.PLUGIN / "scripts"))
import codex_ui
import sdlc

SID = test_codex.SID


class FragmentParser(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.buttons = []
        self.scripts = 0
        self.progress = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button":
            self.buttons.append(attrs)
        if tag == "script":
            self.scripts += 1
        if attrs.get("role") == "progressbar":
            self.progress.append(attrs)


class ChatUiTest(unittest.TestCase):
    setUp = test_codex.CodexHookTest.setUp
    tearDown = test_codex.CodexHookTest.tearDown
    path = test_codex.CodexHookTest.path
    write = test_codex.CodexHookTest.write
    cli = test_codex.CodexHookTest.cli
    hook = test_codex.CodexHookTest.hook
    prompt = test_codex.CodexHookTest.prompt
    to_build = test_codex.CodexHookTest.to_build
    state = test_codex.CodexHookTest.state

    def card(self, sid=SID):
        args = ("chat", "--session", sid) if sid else ("chat",)
        code, markdown = self.cli(*args)
        self.assertEqual(code, 0, markdown)
        # Read the generated transport prompts, then send them as human messages.
        prompts = [json.loads(match) for match in re.findall(
            r':codex-followup\[[^\]]+\]\{prompt=("(?:[^"\\]|\\.)*")\}', markdown)]
        return markdown, prompts

    def action(self, action, target=None, sid=SID):
        _, prompts = self.card(sid)
        return next(prompt for prompt in prompts if prompt.startswith("sdlc ui " + action + " ")
                    and (target is None or prompt.split()[3] == target))

    def approvals(self, slug="feat-a"):
        path = Path(self.path("docs/sdlc/" + slug + "/approvals.json"))
        return json.loads(path.read_text()) if path.exists() else {}

    def test_disabled_card_offers_init_without_creating_files(self):
        os.remove(self.path("sdlc.config.json"))
        markdown, prompts = self.card()
        self.assertIn("Not enabled", markdown)
        self.assertEqual(len(prompts), 1)
        self.assertIn("$ai-sdlc:init", prompts[0])
        self.assertFalse(os.path.exists(self.path(".sdlc")))

    def test_render_is_read_only_and_does_not_start_a_feature(self):
        markdown, prompts = self.card()
        self.assertIn("Code gate: closed", markdown)
        self.assertTrue(any("capture-intent" in prompt for prompt in prompts))
        self.assertFalse(os.path.exists(self.path(".sdlc")))
        self.assertFalse(os.path.exists(self.path("docs/sdlc")))

    def test_missing_session_has_no_mutating_buttons(self):
        self.cli("new", "feat-a")
        _, prompts = self.card(sid=None)
        self.assertEqual(prompts, ["sdlc ui refresh"])

    def test_approval_button_uses_normal_hash_bound_approval_and_audit(self):
        self.cli("new", "feat-a")
        message = self.prompt(self.action("approve-intent"))
        digest = sdlc.sha256_file(self.path("docs/sdlc/feat-a/intent.md"))
        self.assertIn("APPROVED intent", message)
        self.assertEqual(self.approvals()["intent"]["sha256"], digest)
        records = [json.loads(line) for line in Path(self.path(".sdlc/audit.log")).read_text().splitlines()]
        self.assertEqual(sum(row["event"] == "approve" for row in records), 1)
        self.assertTrue(any(row["event"] == "ui-action" for row in records))

    def test_old_button_cannot_approve_a_changed_document(self):
        self.cli("new", "feat-a")
        old = self.action("approve-intent")
        self.write("docs/sdlc/feat-a/intent.md", "# changed intent\n")
        self.assertIn("stale", self.prompt(old))
        self.assertNotIn("intent", self.approvals())
        self.assertIn("APPROVED intent", self.prompt(self.action("approve-intent")))

    def test_old_button_cannot_approve_another_active_feature(self):
        self.cli("new", "feat-a")
        old = self.action("approve-intent")
        self.cli("new", "feat-b")
        self.prompt("sdlc feature feat-b")
        self.assertIn("stale", self.prompt(old))
        self.assertEqual(self.approvals("feat-a"), {})
        self.assertEqual(self.approvals("feat-b"), {})

    def test_buttons_cannot_be_reused_in_another_session(self):
        self.cli("new", "feat-a")
        old = self.action("approve-intent")
        out = self.hook("prompt", session_id="different-session", prompt=old)
        self.assertIn("stale", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.approvals(), {})

    def test_duplicate_click_does_not_record_a_second_approval(self):
        self.cli("new", "feat-a")
        old = self.action("approve-intent")
        self.prompt(old)
        approved = self.approvals()
        self.assertIn("stale", self.prompt(old))
        self.assertEqual(self.approvals(), approved)

    def test_relocked_tests_reject_an_old_unlock_button(self):
        self.to_build()
        self.cli("lock-tests")
        old = self.action("unlock-tests")
        self.cli("lock-tests")
        self.assertIn("stale", self.prompt(old))
        self.assertTrue(self.state()["tests_locked"])
        self.prompt(self.action("unlock-tests"))
        self.assertFalse(self.state()["tests_locked"])

    def test_old_close_button_does_not_close_changed_code(self):
        self.to_build()
        self.hook("post-tool", tool_name="Bash", tool_input={"command": "gh pr create --fill"},
                  tool_response="https://github.com/acme/project/pull/12")
        old = self.action("close-feature")
        self.write("src/app.js", "changed\n")
        self.assertIn("stale", self.prompt(old))
        self.assertNotIn("done", self.approvals())
        self.prompt(self.action("close-feature"))
        self.assertIn("done", self.approvals())

    def test_feature_switch_button_uses_the_displayed_target(self):
        self.cli("new", "feat-a")
        self.cli("new", "feat-b")
        self.prompt(self.action("switch-feature", target="feat-b"))
        self.assertEqual(self.state()["active"], "feat-b")

    def test_skill_button_only_requests_the_corresponding_skill(self):
        self.cli("new", "feat-a")
        self.prompt("sdlc approve intent")
        message = self.prompt(self.action("write-spec"))
        self.assertIn("ai-sdlc:spec", message)
        self.assertFalse(os.path.exists(self.path("docs/sdlc/feat-a/spec.md")))
        self.assertNotIn("spec", self.approvals())

    def test_refresh_does_not_start_or_resume_work(self):
        message = self.prompt("sdlc ui refresh")
        self.assertIn("status only", message)
        self.assertFalse(os.path.exists(self.path("docs/sdlc")))

    def test_malformed_and_unavailable_actions_do_nothing(self):
        self.cli("new", "feat-a")
        valid = self.action("approve-intent")
        unavailable = valid.replace("approve-intent", "approve-plan")
        for line in ("sdlc ui", "sdlc ui bad", unavailable, valid[:-1]):
            with self.subTest(line=line):
                self.prompt(line)
                self.assertEqual(self.approvals(), {})

    def test_agent_cli_cannot_execute_ui_commands_or_relay_them(self):
        code, output = self.cli("ui", "approve-intent", "feat-a", "x")
        self.assertNotEqual(code, 0)
        self.assertIn("user-only", output)
        out = self.hook("pre-tool", tool_name="Bash",
                        tool_input={"command": 'codex exec "sdlc ui approve-intent feat-a x"'})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_core_rechecks_bound_feature_and_document_before_approval(self):
        self.cli("new", "feat-a")
        digest = sdlc.sha256_file(self.path("docs/sdlc/feat-a/intent.md"))
        self.prompt("sdlc approve intent --feature feat-b --sha256 " + digest)
        self.assertEqual(self.approvals(), {})
        self.prompt("sdlc approve intent --feature feat-a --sha256 " + "0" * 64)
        self.assertEqual(self.approvals(), {})
        self.prompt("sdlc approve intent --feature feat-a")
        self.assertEqual(self.approvals(), {})
        self.prompt("sdlc approve intent --feature feat-a --sha256 " + digest)
        self.assertEqual(self.approvals()["intent"]["sha256"], digest)

    def test_artifact_links_and_plan_progress_are_current(self):
        self.to_build()
        self.write("docs/sdlc/feat-a/plan.md", "# plan\n## Work sequence\n1. [ ] First\n2. [ ] Second\n")
        self.prompt("sdlc approve plan")
        self.write("docs/sdlc/feat-a/progress.md", "- [x] Step 1: done\n- [x] Step 9: unrelated\n")
        markdown, _ = self.card()
        self.assertIn("1/2 steps", markdown)
        self.assertIn(self.path("docs/sdlc/feat-a/plan.md"), markdown)
        self.assertIn("sdlc ui build", markdown)

    def test_verification_pass_is_not_presented_as_current_after_code_changes(self):
        self.to_build()
        self.write("src/app.js", "first change\n")
        self.hook("stop")
        state, _ = codex_ui.view(sdlc.Project(self.root), SID)
        self.assertTrue(state["verify"]["current"])
        self.write("src/app.js", "second change\n")
        state, _ = codex_ui.view(sdlc.Project(self.root), SID)
        self.assertFalse(state["verify"]["current"])
        markdown, _ = self.card()
        self.assertIn("previous change", markdown)

    def test_stop_verification_does_not_expire_a_still_valid_button(self):
        self.to_build()
        self.write("src/app.js", "changed\n")
        button = self.action("verify")
        self.hook("stop")
        message = self.prompt(button)
        self.assertNotIn("This chat button is stale", message)
        self.assertIn("ai-sdlc:verify", message)

    def test_empty_verification_config_does_not_trap_the_workflow(self):
        self.write("sdlc.config.json", json.dumps({"verify": []}))
        self.to_build()
        self.write("src/app.js", "changed\n")
        markdown, _ = self.card()
        self.assertIn("not configured", markdown)
        self.assertIn("sdlc ui review", markdown)

    def test_a_commit_keeps_the_verification_current(self):
        # The review stage commits the change first: that must not send it back to verify.
        test_codex.test_hooks.sh(self.root, "git", "checkout", "-qb", "feat/a")
        self.to_build()
        self.write("src/app.js", "changed\n")
        self.hook("stop")
        test_codex.test_hooks.sh(self.root, "git", "add", "-A")
        test_codex.test_hooks.sh(self.root, "git", "commit", "-qm", "work")
        state, _ = codex_ui.view(sdlc.Project(self.root), SID)
        self.assertTrue(state["verify"]["current"])
        for marker in ("SDLC REVIEW: feat-a", "SDLC SECURITY REVIEW: feat-a"):
            self.hook("subagent-stop", agent_type="default",
                      last_assistant_message=marker + "\nREADY FOR HUMAN REVIEW")
        markdown, _ = self.card()
        self.assertIn("sdlc ui create-pr", markdown)
        # A later code change still makes the verification stale.
        self.write("src/app.js", "changed again\n")
        state, _ = codex_ui.view(sdlc.Project(self.root), SID)
        self.assertFalse(state["verify"]["current"])

    def test_a_commit_keeps_the_verification_current_without_a_known_default_branch(self):
        # No origin/HEAD, main or master: the base falls back to HEAD, which the commit moves.
        test_codex.test_hooks.sh(self.root, "git", "branch", "-m", "trunk")
        self.to_build()
        self.write("src/app.js", "changed\n")
        self.hook("stop")
        test_codex.test_hooks.sh(self.root, "git", "add", "-A")
        test_codex.test_hooks.sh(self.root, "git", "commit", "-qm", "work")
        state, _ = codex_ui.view(sdlc.Project(self.root), SID)
        self.assertTrue(state["verify"]["current"])
        self.write("src/app.js", "changed again\n")
        test_codex.test_hooks.sh(self.root, "git", "add", "-A")
        test_codex.test_hooks.sh(self.root, "git", "commit", "-qm", "more work")
        state, _ = codex_ui.view(sdlc.Project(self.root), SID)
        self.assertFalse(state["verify"]["current"])

    def test_create_pr_waits_for_both_reviews(self):
        self.write("sdlc.config.json", json.dumps({"verify": []}))
        self.to_build()
        self.write("src/app.js", "changed\n")
        self.hook("subagent-stop", agent_type="default",
                  last_assistant_message="SDLC REVIEW: feat-a\nREADY FOR HUMAN REVIEW")
        markdown, _ = self.card()
        self.assertIn("Review: ready · Security: not run", markdown)
        self.assertIn("sdlc ui review", markdown)
        self.assertNotIn("sdlc ui create-pr", markdown)
        self.hook("subagent-stop", agent_type="default",
                  last_assistant_message="SDLC SECURITY REVIEW: feat-a\nCHANGES REQUIRED")
        source, _ = self.html_card(language="uk")
        self.assertIn("<dt>Безпека:</dt><dd>потрібні зміни</dd>", source)
        self.hook("subagent-stop", agent_type="default",
                  last_assistant_message="SDLC SECURITY REVIEW: feat-a\nREADY FOR HUMAN REVIEW")
        markdown, _ = self.card()
        self.assertIn("sdlc ui create-pr", markdown)

    def test_prompt_context_refreshes_the_card_in_the_correct_session(self):
        message = self.prompt("continue")
        self.assertIn("chat --session " + SID, message)
        self.assertIn(" --html", message)
        self.assertIn('visualize{"path":', message)
        self.assertIn("Do not substitute a Markdown list", message)
        self.assertIn("Never execute button", message)
        self.assertIn("Do not add cards to subagent reports", message)

    def html_card(self, sid=SID, language="en"):
        args = ["chat", "--html", "--language", language]
        if sid:
            args += ["--session", sid]
        code, source = self.cli(*args)
        self.assertEqual(code, 0, source)
        return source, FragmentParser(source)

    def test_html_disabled_card_has_real_buttons_and_remains_read_only(self):
        os.remove(self.path("sdlc.config.json"))
        source, parsed = self.html_card()
        self.assertEqual(len(parsed.buttons), 2)
        self.assertIn("$ai-sdlc:init", parsed.buttons[0]["data-prompt"])
        self.assertIn("$ai-sdlc:status", parsed.buttons[1]["data-prompt"])
        self.assertEqual(parsed.scripts, 1)
        self.assertNotIn(":codex-followup", source)
        self.assertNotIn("<!doctype", source.lower())
        self.assertIn("window.openai.sendFollowUpMessage", source)
        self.assertFalse(os.path.exists(self.path(".sdlc")))

    def test_html_approval_reaches_shared_handler_and_rejects_changed_document(self):
        self.cli("new", "feat-a")
        _, parsed = self.html_card()
        prompt = next(button["data-prompt"] for button in parsed.buttons
                      if button["data-prompt"].startswith("sdlc ui approve-intent "))
        self.write("docs/sdlc/feat-a/intent.md", "# Updated intent\n")
        self.assertIn("stale", self.prompt(prompt))
        self.assertEqual(self.approvals(), {})
        _, parsed = self.html_card()
        prompt = next(button["data-prompt"] for button in parsed.buttons
                      if button["data-prompt"].startswith("sdlc ui approve-intent "))
        self.assertIn("APPROVED intent", self.prompt(prompt))

    def test_html_localization_does_not_change_bound_prompts(self):
        self.cli("new", "feat-a")
        prompts = []
        for language, label in (("en", "Approve intent"), ("uk", "Погодити intent"),
                                ("ru", "Утвердить intent")):
            source, parsed = self.html_card(language=language)
            self.assertIn(label, source)
            self.assertIn('lang="%s"' % language, source)
            prompts.append([button["data-prompt"] for button in parsed.buttons])
        self.assertEqual(prompts[0], prompts[1])
        self.assertEqual(prompts[1], prompts[2])

    def test_html_without_session_does_not_offer_mutating_actions(self):
        self.cli("new", "feat-a")
        source, parsed = self.html_card(sid=None)
        commands = [button["data-prompt"] for button in parsed.buttons
                    if button["data-prompt"].startswith("sdlc ui ")]
        self.assertEqual(commands, ["sdlc ui refresh"])
        self.assertIn("Session context is unavailable", source)

    def test_html_progress_and_document_requests_use_current_state(self):
        self.to_build()
        self.write("docs/sdlc/feat-a/plan.md", "# plan\n## Work sequence\n1. [ ] First\n2. [ ] Second\n")
        self.prompt("sdlc approve plan")
        self.write("docs/sdlc/feat-a/progress.md", "- [x] Step 1: done\n")
        _, parsed = self.html_card()
        self.assertEqual(parsed.progress[0]["aria-valuenow"], "1")
        self.assertEqual(parsed.progress[0]["aria-valuemax"], "2")
        self.assertTrue(any(self.path("docs/sdlc/feat-a/plan.md") in button["data-prompt"]
                            for button in parsed.buttons))

    def test_html_escapes_display_text_attributes_and_unsafe_pr_urls(self):
        self.cli("new", "feat-a")
        project = sdlc.Project(self.root)
        state, token = codex_ui.view(project, SID)
        state["active"] = '</h2><script>alert("x")</script>'
        state["pr"] = {"url": "javascript:alert(1)"}
        with patch.object(codex_ui, "view", return_value=(state, token)):
            source = codex_ui.render_html(project, SID)
        parsed = FragmentParser(source)
        self.assertEqual(parsed.scripts, 1)
        self.assertNotIn('href="javascript:', source)
        self.assertIn("&lt;/h2&gt;", source)
        self.assertTrue(any(state["active"] in button["data-prompt"] for button in parsed.buttons))

    def test_chat_rejects_unknown_format_flags_without_changing_workflow(self):
        for args in (("--html", "--language", "unknown"), ("--session",), ("--unknown",)):
            code, _ = self.cli("chat", *args)
            self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.path(".sdlc")))

    def test_saving_display_fragment_in_artifacts_does_not_expire_its_action(self):
        self.cli("new", "feat-a")
        source, parsed = self.html_card()
        prompt = next(button["data-prompt"] for button in parsed.buttons
                      if button["data-prompt"].startswith("sdlc ui approve-intent "))
        self.write("docs/sdlc/_ui/sdlc-status.html", source)
        self.assertIn("APPROVED intent", self.prompt(prompt))

    @unittest.skipUnless(os.environ.get("SDLC_CODEX_UI_BROWSER_CHECK") == "1" and shutil.which("node"),
                         "set SDLC_CODEX_UI_BROWSER_CHECK=1 with Node and Playwright installed")
    def test_real_browser_buttons_send_exact_requests_and_handle_errors(self):
        self.to_build()
        self.cli("lock-tests")
        self.write("docs/sdlc/feat-a/plan.md", "# plan\n## Work sequence\n1. [ ] First\n2. [ ] Second\n")
        self.prompt("sdlc approve plan")
        self.write("docs/sdlc/feat-a/progress.md", "- [x] Step 1: done\n")
        source, _ = self.html_card(language="uk")
        with tempfile.TemporaryDirectory(prefix="sdlc-ui-browser-") as directory:
            fragment = Path(directory) / "sdlc-status.html"
            fragment.write_text(source, encoding="utf-8")
            result = subprocess.run(["node", str(Path(__file__).with_name("chat-ui-browser.cjs")),
                                     str(fragment)], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
