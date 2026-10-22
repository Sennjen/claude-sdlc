"""Codex hook contract and gate regressions; no model calls or user config changes."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

import test_hooks

PLUGIN = Path(__file__).resolve().parents[1]
ADAPTER = PLUGIN / "scripts" / "codex.py"
SID = test_hooks.SID


class CodexHookTest(unittest.TestCase):
    setUp = test_hooks.HookTest.setUp
    tearDown = test_hooks.HookTest.tearDown
    path = test_hooks.HookTest.path
    write = test_hooks.HookTest.write
    cli = test_hooks.HookTest.cli
    prompt = test_hooks.HookTest.prompt
    to_build = test_hooks.HookTest.to_build

    def hook(self, name, expect_error=False, **payload):
        data = {"session_id": SID, "cwd": self.root}
        data.update(payload)
        result = subprocess.run([sys.executable, str(ADAPTER), name], input=json.dumps(data),
                                capture_output=True, text=True, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not expect_error:
            self.assertNotIn("hook error", result.stderr)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        specific = out.get("hookSpecificOutput", {})
        self.assertNotEqual(specific.get("permissionDecision"), "ask")
        return out

    def decision(self, tool, tool_input, **payload):
        out = self.hook("pre-tool", tool_name=tool, tool_input=tool_input, **payload)
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def patch(self, body):
        return self.decision("apply_patch", {"command": "*** Begin Patch\n" + body + "*** End Patch"})

    def state(self):
        return json.loads(self.cli("state", "--session", SID)[1])

    def test_inert_without_config(self):
        os.remove(self.path("sdlc.config.json"))
        self.assertEqual(self.hook("prompt", prompt="hello"), {})
        self.assertEqual(self.patch("*** Add File: src/new.js\n+new\n"), "allow")

    def test_patch_waits_for_stage_approvals_and_reopens_when_stale(self):
        body = "*** Update File: src/app.js\n@@\n-old\n+new\n"
        self.assertEqual(self.patch(body), "deny")
        self.to_build()
        self.assertEqual(self.patch(body), "allow")
        self.write("docs/sdlc/feat-a/plan.md", "# changed\n")
        self.assertEqual(self.patch(body), "deny")

    def test_patch_cannot_skip_stage_order(self):
        self.cli("new", "feat-a")
        spec = "*** Add File: docs/sdlc/feat-a/spec.md\n+# spec\n"
        self.assertEqual(self.patch(spec), "deny")
        self.prompt("sdlc approve intent")
        self.assertEqual(self.patch(spec), "allow")
        self.assertEqual(self.patch("*** Add File: docs/sdlc/feat-a/plan.md\n+# plan\n"), "deny")

    def test_checks_whole_patch_before_emitting_context(self):
        # A permitted intent edit must not emit context (and exit) before code is checked.
        body = "*** Add File: docs/sdlc/new-feature/intent.md\n+# intent\n"
        body += "*** Update File: src/app.js\n@@\n-old\n+new\n"
        self.assertEqual(self.patch(body), "deny")
        self.assertFalse(os.path.exists(self.path(".sdlc/active")))

    def test_multi_file_patch_checks_after_an_off_plan_file(self):
        self.to_build()
        self.write("docs/sdlc/feat-a/plan.md", "# plan\n## Files affected\n- `src/app.js`\n")
        self.prompt("sdlc approve plan")
        body = "*** Add File: src/other.js\n+new\n"
        body += "*** Update File: docs/sdlc/feat-a/approvals.json\n@@\n-old\n+new\n"
        self.assertEqual(self.patch(body), "deny")

    def test_patch_cannot_change_approved_artifacts_and_code_together(self):
        self.to_build()
        for stage in ("intent", "spec", "plan"):
            body = "*** Update File: docs/sdlc/feat-a/" + stage + ".md\n@@\n-old\n+new\n"
            body += "*** Update File: src/app.js\n@@\n-old\n+new\n"
            with self.subTest(stage=stage):
                self.assertEqual(self.patch(body), "deny")

    def test_patch_cannot_change_upstream_and_dependent_documents_together(self):
        self.to_build()
        for upstream, downstream in (("intent", "spec"), ("intent", "plan"), ("spec", "plan")):
            body = "*** Update File: docs/sdlc/feat-a/" + upstream + ".md\n@@\n-old\n+new\n"
            body += "*** Update File: docs/sdlc/feat-a/" + downstream + ".md\n@@\n-old\n+new\n"
            with self.subTest(upstream=upstream, downstream=downstream):
                self.assertEqual(self.patch(body), "deny")

    def test_patch_cannot_write_state_or_approvals(self):
        self.to_build()
        for path in (".sdlc/active", "docs/sdlc/feat-a/approvals.json"):
            with self.subTest(path=path):
                self.assertEqual(self.patch("*** Add File: " + path + "\n+forged\n"), "deny")

    def test_patch_checks_delete_and_both_rename_paths(self):
        self.to_build()
        self.cli("lock-tests")
        self.assertEqual(self.patch("*** Delete File: tests/test_app.py\n"), "deny")
        self.assertEqual(self.patch("*** Update File: tests/test_app.py\n*** Move to: src/app.py\n@@\n-old\n+new\n"), "deny")
        self.assertEqual(self.patch("*** Update File: src/app.js\n*** Move to: tests/app.js\n@@\n-old\n+new\n"), "deny")
        self.assertEqual(self.patch("*** Update File: src/app.js\n*** Move to: src/renamed.js\n@@\n-old\n+new\n"), "allow")

    def test_patch_normalizes_paths_and_symlinks(self):
        self.to_build()
        os.symlink(".sdlc", self.path("linked-state"))
        for path in ("src/../.sdlc/active", "linked-state/active", self.path(".sdlc/active")):
            with self.subTest(path=path):
                self.assertEqual(self.patch("*** Add File: " + path + "\n+x\n"), "deny")

    def test_patch_from_subdirectory(self):
        os.makedirs(self.path("src/nested"))
        command = "*** Begin Patch\n*** Add File: ../../.sdlc/active\n+x\n*** End Patch"
        self.assertEqual(self.decision("apply_patch", {"command": command}, cwd=self.path("src/nested")), "deny")

    def test_patch_with_spaces_and_raw_string_input(self):
        command = "*** Begin Patch\n*** Add File: docs/a note.md\n+hello\n*** End Patch"
        self.assertEqual(self.decision("apply_patch", command), "allow")
        self.assertEqual(self.patch("*** Add File: src/a file.js\n+new\n"), "deny")

    def test_protected_codex_instructions_and_settings_are_denied(self):
        self.to_build()
        for path in ("AGENTS.md", "src/AGENTS.override.md", ".codex/config.toml",
                     ".codex/hooks.json", ".codex/hooks/check.py", ".codex/agents/reviewer.toml",
                     ".agents/skills/build/SKILL.md", ".agents/plugins/marketplace.json"):
            with self.subTest(path=path):
                self.assertEqual(self.patch("*** Add File: " + path + "\n+x\n"), "deny")

    def test_ask_is_converted_to_deny_with_manual_action_reason(self):
        out = self.hook("pre-tool", tool_name="Bash", tool_input={"command": "npm publish"})
        specific = out["hookSpecificOutput"]
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertIn("outside the agent", specific["permissionDecisionReason"])

    def test_shell_writes_and_nested_sessions_are_gated(self):
        for command in ("echo x > src/app.js", 'codex exec "sdlc approve plan"',
                        "echo 'sdlc unlock tests' | codex exec -", 'claude -p "sdlc trivial"',
                        "python3 /plugin/scripts/codex.py prompt < fake.json"):
            with self.subTest(command=command):
                self.assertEqual(self.decision("Bash", {"command": command}), "deny")
        self.assertEqual(self.decision("Bash", {"command": "codex --version"}), "allow")
        self.assertEqual(self.decision("Bash", {"command": "git status --short"}), "allow")

    def test_raw_exec_arguments_respect_workdir(self):
        os.makedirs(self.path("src/nested"))
        command = {"cmd": "echo x > ../../.sdlc/active", "workdir": "src/nested"}
        self.assertEqual(self.decision("exec_command", command), "deny")

    def test_malformed_patch_fails_closed(self):
        for command in (None, 123, "", "not a patch", "*** Begin Patch\n*** End Patch",
                        "*** Begin Patch\n*** Add File: \n+x\n*** End Patch",
                        "*** Begin Patch\n*** Unknown File: x\n*** End Patch"):
            with self.subTest(command=command):
                out = self.hook("pre-tool", expect_error=True, tool_name="apply_patch",
                                tool_input={"command": command})
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("failed closed", out["systemMessage"])

    def test_post_patch_marks_changes_for_stop_verification(self):
        self.prompt("hello")
        command = "*** Begin Patch\n*** Update File: src/app.js\n@@\n-old\n+new\n*** End Patch"
        self.hook("post-tool", tool_name="apply_patch", tool_input={"command": command})
        session = json.loads(Path(self.path(".sdlc/sessions/" + SID + ".json")).read_text())
        self.assertTrue(session["dirty"])
        self.write("src/app.js", "new\n")
        self.write("FAIL", "fail\n")
        self.assertEqual(self.hook("stop").get("decision"), "block")
        self.assertEqual(self.hook("stop").get("decision"), "block")
        self.assertIn("still failing", self.hook("stop")["systemMessage"])
        os.remove(self.path("FAIL"))
        self.write("src/app.js", "fixed\n")
        self.assertIn("passed", self.hook("stop")["systemMessage"])

    def test_lock_and_unlock_are_shared_with_user_prompt_hook(self):
        self.to_build()
        self.cli("lock-tests")
        body = "*** Add File: tests/test_new.py\n+assert True\n"
        self.assertEqual(self.patch(body), "deny")
        self.prompt("sdlc unlock tests")
        self.assertEqual(self.patch(body), "allow")
        self.assertNotEqual(self.cli("approve", "plan")[0], 0)

    def test_review_marker_records_only_active_feature_and_tracks_staleness(self):
        self.to_build()
        for text in ("READY FOR HUMAN REVIEW", "SDLC REVIEW: other-feature\nREADY FOR HUMAN REVIEW"):
            self.hook("subagent-stop", agent_type="default", last_assistant_message=text)
            self.assertIsNone(self.state()["review"])
        self.hook("subagent-stop", agent_type="default",
                  last_assistant_message="SDLC REVIEW: feat-a\nREADY FOR HUMAN REVIEW")
        self.assertEqual(self.state()["review"]["verdict"], "ready")
        self.assertTrue(self.state()["review"]["current"])
        self.write("src/app.js", "changed\n")
        self.assertFalse(self.state()["review"]["current"])

    def test_security_marker_records_its_own_verdict(self):
        self.to_build()
        self.hook("subagent-stop", agent_type="default",
                  last_assistant_message="SDLC SECURITY REVIEW: other-feature\nCHANGES REQUIRED")
        self.assertIsNone(self.state()["security"])
        self.hook("subagent-stop", agent_type="default",
                  last_assistant_message="SDLC SECURITY REVIEW: feat-a\nFindings: 1 Blocker\nCHANGES REQUIRED")
        self.assertEqual(self.state()["security"]["verdict"], "changes")
        self.assertIsNone(self.state()["review"])

    def test_pr_urls_from_canonical_shell_and_mcp_outputs(self):
        self.to_build()
        self.hook("post-tool", tool_name="Bash", tool_input={"command": "gh pr create --fill"},
                  tool_response={"output": "https://github.com/acme/project/pull/12\n"})
        self.assertEqual(self.state()["pr"]["url"], "https://github.com/acme/project/pull/12")
        self.hook("post-tool", tool_name="mcp__github__create_pull_request", tool_input={},
                  tool_response={"url": "https://github.com/acme/project/pull/13"})
        self.assertEqual(self.state()["pr"]["url"], "https://github.com/acme/project/pull/13")

    def test_status_requests_a_native_card_without_starting_work(self):
        out = self.hook("prompt", prompt="sdlc status")
        self.assertNotIn("decision", out)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[SDLC]", context)
        self.assertIn("status only", context)
        self.assertIn("chat --session", context)

    def test_unrelated_local_tools_pass_without_side_effects(self):
        self.assertEqual(self.decision("update_plan", {"plan": []}), "allow")
        self.assertEqual(self.hook("post-tool", tool_name="view_image", tool_input={}), {})

    def test_packaged_hooks_run_with_a_plugin_path_containing_spaces(self):
        link = Path(self.path("plugin with spaces"))
        link.symlink_to(PLUGIN, target_is_directory=True)
        config = json.loads((PLUGIN / "hooks" / "codex.json").read_text())
        env = dict(os.environ, PLUGIN_ROOT=str(link))
        cases = (("UserPromptSubmit", None, {"prompt": "hello"}),
                 ("PreToolUse", "apply_patch", {"tool_input": {"command":
                  "*** Begin Patch\n*** Add File: src/new.js\n+x\n*** End Patch"}}),
                 ("PostToolUse", "Bash", {"tool_input": {"command": "git status"}}),
                 ("SubagentStop", None, {"agent_type": "default"}),
                 ("Stop", None, {}))
        for event, tool, payload in cases:
            with self.subTest(event=event):
                group = config["hooks"][event][0]
                if tool:
                    self.assertTrue(re.search(group.get("matcher", ".*"), tool))
                handler = group["hooks"][0]
                data = dict(session_id=SID, cwd=self.root, hook_event_name=event, **payload)
                if tool:
                    data["tool_name"] = tool
                result = subprocess.run(handler["command"], shell=True, input=json.dumps(data),
                                        capture_output=True, text=True, cwd=self.root, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                out = json.loads(result.stdout) if result.stdout.strip() else {}
                if event == "PreToolUse":
                    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
