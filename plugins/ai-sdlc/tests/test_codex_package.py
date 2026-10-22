"""Optional real Codex package discovery test. Never starts a model turn.

SDLC_CODEX_PACKAGE_CHECK=1 python3 -m unittest discover -s plugins/ai-sdlc/tests -v
"""
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get("SDLC_CODEX_PACKAGE_CHECK") == "1" and shutil.which("codex"),
                     "set SDLC_CODEX_PACKAGE_CHECK=1 with Codex CLI installed")
class CodexPackageTest(unittest.TestCase):
    def test_real_loader_discovers_all_skills_and_only_codex_hooks(self):
        root = Path(__file__).resolve().parents[3]
        expected = {"ai-sdlc:" + name for name in
                    ("init", "intent", "spec", "plan", "build", "verify", "review", "maintain", "status")}
        with tempfile.TemporaryDirectory(prefix="sdlc-codex-package-") as directory:
            env = dict(os.environ, CODEX_HOME=directory)
            for args in (("plugin", "marketplace", "add", str(root)),
                         ("plugin", "add", "ai-sdlc@ai-sdlc")):
                result = subprocess.run(["codex", *args], env=env, cwd=root, capture_output=True,
                                        text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            installed = list(Path(directory).glob("plugins/cache/ai-sdlc/ai-sdlc/*/bin/sdlc"))
            self.assertEqual(len(installed), 1)
            rendered = subprocess.run([str(installed[0]), "chat", "--html"], env=env, cwd=root,
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            self.assertIn('<button type="button"', rendered.stdout)
            self.assertIn("window.openai.sendFollowUpMessage", rendered.stdout)
            with tempfile.TemporaryFile() as stderr:
                process = subprocess.Popen(["codex", "app-server"], env=env, cwd=root,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
                buffer = b""

                def send(message):
                    process.stdin.write((json.dumps(message) + "\n").encode())
                    process.stdin.flush()

                def rpc(identifier, method, params):
                    nonlocal buffer
                    send({"id": identifier, "method": method, "params": params})
                    deadline = time.monotonic() + 35
                    while time.monotonic() < deadline:
                        if b"\n" not in buffer:
                            if not select.select([process.stdout], [], [], 1)[0]:
                                continue
                            chunk = os.read(process.stdout.fileno(), 65536)
                            if not chunk:
                                self.fail("Codex app-server exited before replying to " + method)
                            buffer += chunk
                            continue
                        line, buffer = buffer.split(b"\n", 1)
                        reply = json.loads(line)
                        if reply.get("id") == identifier:
                            self.assertNotIn("error", reply, reply)
                            return reply["result"]
                    self.fail("Codex app-server timed out: " + method)

                try:
                    rpc(1, "initialize", {"clientInfo": {"name": "sdlc-package-test", "version": "1.0.0"},
                                           "capabilities": {"experimentalApi": True}})
                    send({"method": "initialized"})
                    plugin = rpc(2, "plugin/read", {
                        "pluginName": "ai-sdlc",
                        "marketplacePath": str(root / ".agents/plugins/marketplace.json")})["plugin"]
                    self.assertEqual({skill["name"] for skill in plugin["skills"]}, expected)
                    self.assertEqual({hook["eventName"] for hook in plugin["hooks"]},
                                     {"preToolUse", "postToolUse", "userPromptSubmit", "subagentStop", "stop"})
                    self.assertEqual(len(plugin["hooks"]), 5)
                    self.assertTrue(all("hooks/codex.json" in hook["key"] for hook in plugin["hooks"]))
                    entries = rpc(3, "skills/list", {"cwds": [str(root)], "forceReload": True})["data"]
                    self.assertTrue(entries)
                    for entry in entries:
                        self.assertEqual(entry["errors"], [])
                        discovered = {skill["name"] for skill in entry["skills"]}
                        self.assertTrue(expected <= discovered, expected - discovered)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    process.stdin.close()
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
