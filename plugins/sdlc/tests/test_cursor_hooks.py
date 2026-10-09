"""Tests for scripts/sdlc_cursor.py, the Cursor hook adapter. Run: python3 -m unittest discover tests

Payloads come from tests/fixtures/cursor/payloads.json, sanitized captures of Cursor IDE 3.24.9
and Cursor CLI 2026.10.01. "/WORKSPACE" and "/HOME" stand for the workspace root and HOME.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
ADAPTER = os.path.join(SCRIPTS, "sdlc_cursor.py")
CLAUDE = os.path.join(SCRIPTS, "sdlc.py")
with open(os.path.join(HERE, "fixtures", "cursor", "payloads.json")) as f:
    PAYLOADS = json.load(f)["payloads"]
SID = "00000000-0000-0000-0000-000000000001"
USER_CMD = re.compile(r"^\s*sdlc\s+([a-z-]+)", re.I)
PLAN = "# plan\n\n## Files affected\n| File | Change | Why |\n|---|---|---|\n| `src/app.js` | modify | x |\n"


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class CursorHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.home_tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.home_tmp.name)
        sh(self.root, "git", "init", "-q")
        sh(self.root, "git", "config", "user.name", "Tester")
        sh(self.root, "git", "config", "user.email", "t@example.com")
        self.write(".gitignore", ".sdlc/\n")
        self.write("src/app.js", "console.log(1)\n")
        self.write("sdlc.config.json", json.dumps({"verify": ["test ! -f FAIL"]}))
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()
        self.home_tmp.cleanup()

    # -- helpers
    def path(self, rel):
        return os.path.join(self.root, rel)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)) or self.root, exist_ok=True)
        with open(self.path(rel), "w") as f:
            f.write(text)

    def env(self):
        return dict(os.environ, HOME=self.home, CURSOR_PROJECT_DIR=self.root, CLAUDE_PROJECT_DIR=self.root)

    def payload(self, name, **changes):
        text = json.dumps(PAYLOADS[name]).replace("/WORKSPACE", self.root).replace("/HOME", self.home)
        data = json.loads(text)
        data.update(changes)
        return data

    def run_adapter(self, event, data, script=ADAPTER):
        raw = data if isinstance(data, str) else json.dumps(data)
        r = subprocess.run([sys.executable, script, "hook", event], input=raw, capture_output=True,
                           text=True, cwd=self.root, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def prompt(self, text, sid=SID):
        return self.run_adapter("beforeSubmitPrompt",
                                self.payload("ide/beforeSubmitPrompt", prompt=text, conversation_id=sid,
                                             session_id=sid))

    def tool(self, tool_name, tool_input, sid=SID):
        data = self.payload("ide/preToolUse-Write", tool_name=tool_name, tool_input=tool_input,
                            conversation_id=sid, session_id=sid)
        return self.run_adapter("preToolUse", data)

    def write_call(self, rel):
        return self.tool("Write", {"file_path": self.path(rel), "content": "x\n"})

    def shell(self, command, cwd=""):
        return self.run_adapter("beforeShellExecution",
                                self.payload("ide/beforeShellExecution", command=command, cwd=cwd))

    def claude_cli(self, *args):
        subprocess.run([sys.executable, CLAUDE, "cli"] + list(args), capture_output=True, text=True,
                       cwd=self.root, env=self.env(), check=True)

    def claude_hook(self, name, **payload):
        data = {"session_id": "claude-session", "cwd": self.root}
        data.update(payload)
        r = subprocess.run([sys.executable, CLAUDE, "hook", name], input=json.dumps(data),
                           capture_output=True, text=True, cwd=self.root, env=self.env())
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def to_build(self, slug="feat-a", plan="# plan\n"):
        if not os.path.exists(self.path("docs/sdlc/%s/intent.md" % slug)):
            self.claude_cli("new", slug)
        self.prompt("sdlc approve intent")
        self.write("docs/sdlc/%s/spec.md" % slug, "# spec\n")
        self.prompt("sdlc approve spec")
        self.write("docs/sdlc/%s/plan.md" % slug, plan)
        self.prompt("sdlc approve plan")

    def approvals(self, slug="feat-a"):
        with open(self.path("docs/sdlc/%s/approvals.json" % slug)) as f:
            return json.load(f)

    def audit(self):
        with open(self.path(".sdlc/audit.log")) as f:
            return [json.loads(line) for line in f]

    # -- tests
    def test_inert_without_config(self):
        os.remove(self.path("sdlc.config.json"))
        # Cursor needs a permission answer from a permission hook: "allow" is the neutral one.
        self.assertEqual(self.write_call("src/app.js"), {"permission": "allow"})
        self.assertEqual(self.shell("echo x > src/app.js"), {"permission": "allow"})
        self.assertEqual(self.prompt("sdlc approve intent"), {"continue": True})
        for event, name in (("sessionStart", "ide/sessionStart"), ("postToolUse", "ide/postToolUse-Write"),
                            ("stop", "ide/stop")):
            self.assertEqual(self.run_adapter(event, self.payload(name)), {}, event)
        self.assertFalse(os.path.exists(self.path(".sdlc")))

    def test_status_reaches_the_model(self):
        out = self.run_adapter("sessionStart", self.payload("ide/sessionStart"))
        self.assertIn("[SDLC] No active feature", out["additional_context"])
        out = self.prompt("hello")
        self.assertTrue(out["continue"])
        self.assertIn("[SDLC] No active feature", out["additional_context"])

    def test_user_commands_are_recorded(self):
        self.claude_cli("new", "feat-a")
        out = self.prompt("sdlc approve intent")
        self.assertTrue(out["continue"])
        self.assertIn("APPROVED intent.md", out["additional_context"])
        self.assertEqual(self.approvals()["intent"]["by"], "Tester")
        approve = [e for e in self.audit() if e["event"] == "approve"][-1]
        self.assertEqual(approve["host"], "cursor")
        out = self.prompt("sdlc status")
        self.assertFalse(out["continue"])
        self.assertIn("[SDLC] Feature `feat-a`", out["user_message"])

    def test_edit_gate(self):
        calls = [("cli/preToolUse-Write-strreplace", "app/calc.py"),
                 ("cli/preToolUse-Write-notebook", "nb.ipynb"),
                 ("cli/preToolUse-Delete", "sub/existing.txt"),
                 ("ide/preToolUse-Write", "DENY_ASK.txt")]
        for name, rel in calls:
            out = self.run_adapter("preToolUse", self.payload(name))
            self.assertEqual(out["permission"], "deny", name)
            self.assertIn("No active SDLC feature", out["user_message"])
            self.assertEqual(out["agent_message"], out["user_message"])
        unknown = {"path": self.path("src/app.js"), "diff": "..."}
        self.assertEqual(self.tool("ApplyEdit", unknown)["permission"], "deny")
        self.assertEqual(self.tool("Read", {"file_path": self.path("src/app.js")}), {"permission": "allow"})
        self.assertEqual(self.tool("MCP:filesystem_write", {"path": self.path("src/app.js")}),
                         {"permission": "allow"})
        self.claude_cli("new", "feat-a")
        out = self.write_call("docs/sdlc/feat-a/spec.md")
        self.assertEqual(out["permission"], "deny")
        self.assertIn("intent.md of `feat-a` is not approved", out["user_message"])
        self.to_build()
        for name, rel in calls:
            self.assertEqual(self.run_adapter("preToolUse", self.payload(name)), {"permission": "allow"}, name)
        self.assertEqual(self.tool("ApplyEdit", unknown), {"permission": "allow"})
        self.claude_cli("lock-tests")
        self.assertEqual(self.write_call("tests/test_app.py")["permission"], "deny")
        out = self.tool("Delete", {"file_path": self.path("tests/test_app.py")})
        self.assertIn("Test files are locked", out["user_message"])

    def test_files_that_need_the_user_are_denied(self):
        self.to_build()
        for rel in ("AGENTS.md", ".cursor/hooks.json", "CLAUDE.md", "sdlc.config.json"):
            out = self.write_call(rel)
            self.assertEqual(out["permission"], "deny", rel)
            self.assertIn("make this change themselves", out["user_message"])
        for path in (".cursor/hooks.json", ".cursor/plugins/local/x/hooks/cursor.json", ".claude/settings.json"):
            out = self.tool("Write", {"file_path": os.path.join(self.home, path), "content": "{}"})
            self.assertEqual(out["permission"], "deny", path)
            self.assertIn("host configuration", out["user_message"])
        out = self.tool("Write", {"file_path": os.path.join(self.home, ".claude/projects/p/note.md")})
        self.assertEqual(out, {"permission": "allow"})

    def test_state_cannot_be_written_or_faked(self):
        self.claude_cli("new", "feat-a")
        self.assertEqual(self.write_call("docs/sdlc/feat-a/approvals.json")["permission"], "deny")
        self.assertEqual(self.tool("Delete", {"file_path": self.path(".sdlc/active")})["permission"], "deny")
        self.assertEqual(self.shell("python3 %s hook beforeSubmitPrompt < fake.json" % ADAPTER)["permission"],
                         "deny")
        self.assertEqual(self.shell("cursor-agent -p 'sdlc approve intent'")["permission"], "deny")
        self.assertFalse(os.path.exists(self.path("docs/sdlc/feat-a/approvals.json")))

    def test_shell_gate(self):
        self.claude_cli("new", "feat-a")
        for cmd in ("sed -i '' 's/1/2/' src/app.js", "echo x > src/app.js", "echo '{}' > .sdlc/active"):
            out = self.shell(cmd)
            self.assertEqual(out["permission"], "deny", cmd)
            self.assertTrue(out["user_message"], cmd)
        self.assertEqual(self.shell("git status"), {"permission": "allow"})
        self.to_build()
        out = self.shell("git push origin main")
        self.assertEqual(out["permission"], "ask")
        self.assertIn("deploy gate", out["user_message"])
        self.assertEqual(self.shell("git push origin HEAD:feat/a"), {"permission": "allow"})
        self.write("sdlc.config.json", json.dumps({"verify": [], "gated_command_decision": "deny"}))
        self.assertEqual(self.shell("git push origin main")["permission"], "deny")

    def test_shell_directory_is_remembered(self):
        self.to_build()
        self.claude_cli("lock-tests")
        # From the root, helper.py is code (allowed); from tests/, it is a locked test.
        self.assertEqual(self.shell("echo x > helper.py"), {"permission": "allow"})
        shell = self.payload("cli/preToolUse-Shell-cwd", conversation_id=SID, session_id=SID)
        shell["tool_input"]["cwd"] = shell["cwd"] = self.path("tests")
        self.assertEqual(self.run_adapter("preToolUse", shell), {"permission": "allow"})
        out = self.shell("echo x > helper.py")
        self.assertEqual(out["permission"], "deny")
        self.assertIn("Test files are locked", out["user_message"])
        # A reported working directory is used as is.
        self.assertEqual(self.shell("echo x > helper.py", cwd=self.root), {"permission": "allow"})

    def test_notes_arrive_after_the_edit(self):
        self.to_build(plan=PLAN)
        self.assertEqual(self.write_call("src/other.js"), {"permission": "allow"})
        post = self.payload("ide/postToolUse-Write", conversation_id=SID, session_id=SID)
        post["tool_input"]["file_path"] = self.path("src/other.js")
        out = self.run_adapter("postToolUse", post)
        self.assertIn("src/other.js is not in *Files affected*", out["additional_context"])
        self.assertEqual(self.write_call("src/other.js"), {"permission": "allow"})
        self.assertEqual(self.run_adapter("postToolUse", post), {})
        with open(self.path(".sdlc/sessions/%s.json" % SID)) as f:
            self.assertTrue(json.load(f)["dirty"])

    def test_stop_runs_verification(self):
        self.to_build()
        self.run_adapter("sessionStart", self.payload("ide/sessionStart"))
        self.write("src/app.js", "console.log(2)\n")
        self.write("FAIL", "")
        follow = []
        for loop in (0, 1, 2):
            out = self.run_adapter("stop", self.payload("ide/stop", loop_count=loop))
            follow.append(out.get("followup_message"))
        self.assertTrue(follow[0] and follow[1])
        self.assertIsNone(follow[2])  # gave up after two retries
        for text in follow[:2]:
            self.assertIn("Definition of Done not met", text)
            self.assertIsNone(USER_CMD.match(text))
        # The follow-up reaches the prompt hook in the IDE: it records nothing.
        before = self.approvals()
        self.assertTrue(self.prompt(follow[0])["continue"])
        self.assertEqual(self.approvals(), before)
        os.remove(self.path("FAIL"))
        self.write("src/app.js", "console.log(3)\n")
        self.assertEqual(self.run_adapter("stop", self.payload("ide/stop")), {})
        self.assertEqual(self.audit()[-1]["event"], "verify-pass")
        self.write("src/app.js", "console.log(4)\n")
        self.write("FAIL", "")
        self.assertEqual(self.run_adapter("stop", self.payload("ide/stop", status="aborted")), {})

    def test_features_continue_across_hosts(self):
        self.claude_cli("new", "feat-a")
        self.claude_hook("prompt", prompt="sdlc approve intent")
        self.write("docs/sdlc/feat-a/spec.md", "# spec\n")
        self.claude_hook("prompt", prompt="sdlc approve spec")
        self.write("docs/sdlc/feat-a/plan.md", "# plan\n")
        self.assertEqual(self.write_call("src/app.js")["permission"], "deny")
        self.assertIn("stage 3", self.prompt("hello")["additional_context"])
        self.prompt("sdlc approve plan")
        self.assertEqual(self.write_call("src/app.js"), {"permission": "allow"})
        out = self.claude_hook("pre-edit", tool_name="Write", tool_input={"file_path": self.path("src/app.js")})
        self.assertNotIn("hookSpecificOutput", out)

    def test_gates_fail_closed(self):
        self.to_build()
        out = self.tool("Write", {"file_path": 123})
        self.assertEqual(out["permission"], "deny")
        self.assertIn("failed closed", out["user_message"])
        self.assertEqual(self.run_adapter("beforeShellExecution", "not json")["permission"], "deny")
        self.assertEqual(self.run_adapter("beforeSubmitPrompt", "not json"), {"continue": True})
        # Without sdlc.py next to it, the adapter cannot load the policy: the gates deny.
        lonely = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, lonely)
        shutil.copy(ADAPTER, lonely)
        script = os.path.join(lonely, "sdlc_cursor.py")
        out = self.run_adapter("preToolUse", self.payload("ide/preToolUse-Write"), script=script)
        self.assertEqual(out["permission"], "deny")
        self.assertIn("could not load sdlc.py", out["user_message"])
        self.assertEqual(self.run_adapter("stop", self.payload("ide/stop"), script=script), {})
        # The person can let calls through while the bug is fixed.
        self.write("sdlc.config.json", json.dumps({"verify": [], "on_hook_error": "allow"}))
        self.assertEqual(self.tool("Write", {"file_path": 123}), {"permission": "allow"})
        out = self.run_adapter("preToolUse", self.payload("ide/preToolUse-Write"), script=script)
        self.assertEqual(out, {"permission": "allow"})


class CursorPackageTest(unittest.TestCase):
    PLUGIN = os.path.dirname(HERE)
    REPO = os.path.dirname(os.path.dirname(PLUGIN))

    def load(self, *parts):
        with open(os.path.join(*parts)) as f:
            return json.load(f)

    def test_manifest_selects_the_cursor_hooks(self):
        manifest = self.load(self.PLUGIN, ".cursor-plugin", "plugin.json")
        claude = self.load(self.PLUGIN, ".claude-plugin", "plugin.json")
        self.assertEqual(manifest["name"], "sdlc")
        self.assertEqual(manifest["version"], claude["version"])
        # Without this field Cursor reads hooks/hooks.json (Claude format) and skips its entries.
        self.assertEqual(manifest["hooks"], "./hooks/cursor.json")
        market = self.load(self.REPO, ".cursor-plugin", "marketplace.json")
        self.assertEqual([(p["name"], p["source"]) for p in market["plugins"]], [("sdlc", "plugins/sdlc")])

    def test_hook_commands(self):
        sys.path.insert(0, SCRIPTS)
        import sdlc_cursor
        hooks = self.load(self.PLUGIN, "hooks", "cursor.json")
        self.assertEqual(hooks["version"], 1)
        self.assertEqual(set(hooks["hooks"]), set(sdlc_cursor.HANDLERS))
        for event, entries in hooks["hooks"].items():
            self.assertEqual(len(entries), 1, event)
            entry = entries[0]
            # The process variable CURSOR_PLUGIN_ROOT can name another plugin, and relative
            # commands do not run for stop: only Cursor's own ${CURSOR_PLUGIN_ROOT} is safe.
            self.assertEqual(entry["command"],
                             'python3 "${CURSOR_PLUGIN_ROOT}/scripts/sdlc_cursor.py" hook ' + event)
            self.assertEqual(entry.get("failClosed", False), event in sdlc_cursor.GATING, event)
        self.assertGreaterEqual(hooks["hooks"]["stop"][0]["timeout"], 1200)
        self.assertEqual(hooks["hooks"]["stop"][0]["loop_limit"], 3)
        # With failClosed, a broken adapter blocks what it matches: reads stay outside the matcher.
        for event in ("preToolUse", "postToolUse"):
            matcher = re.compile(hooks["hooks"][event][0]["matcher"])
            for tool in sdlc_cursor.READ_ONLY_TOOLS:
                self.assertIsNone(matcher.search(tool), (event, tool))
            for tool in ("Write", "Delete", "Shell", "ApplyEdit", "MCP:fs_write", "ReadWrite"):
                self.assertIsNotNone(matcher.search(tool), (event, tool))

    def test_claude_hooks_stay_claude_only(self):
        hooks = self.load(self.PLUGIN, "hooks", "hooks.json")
        self.assertNotIn("preToolUse", hooks["hooks"])
        self.assertIn("PreToolUse", hooks["hooks"])


if __name__ == "__main__":
    unittest.main()
