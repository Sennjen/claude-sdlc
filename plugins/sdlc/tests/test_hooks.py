"""End-to-end tests for scripts/sdlc.py hooks. Run: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "sdlc.py")
SID = "test-session"
GATED = [r"\bgit\s+push\b", r"\bupload-assets\b", r"\bfastlane\b",
         r"\bdocker[\s-]+compose\b.*\b(up|down|rm)\b"]


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
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

    # -- helpers
    def path(self, rel):
        return os.path.join(self.root, rel)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)) or self.root, exist_ok=True)
        with open(self.path(rel), "w") as f:
            f.write(text)

    def hook(self, name, **payload):
        data = {"session_id": SID, "cwd": self.root}
        data.update(payload)
        # Claude Code sets CLAUDE_PROJECT_DIR for hooks; point it at the test repo so a host
        # project (e.g. this repo with SDLC enabled) never leaks into the hook under test.
        r = subprocess.run([sys.executable, SCRIPT, "hook", name], input=json.dumps(data),
                           capture_output=True, text=True, cwd=self.root,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("hook error", r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "cli"] + list(args), capture_output=True,
                           text=True, cwd=self.root, env=dict(os.environ, CLAUDE_PROJECT_DIR=self.root))
        return r.returncode, r.stdout

    def prompt(self, text):
        out = self.hook("prompt", prompt=text)
        return out["hookSpecificOutput"]["additionalContext"]

    def edit(self, rel):
        out = self.hook("pre-edit", tool_name="Write", tool_input={"file_path": self.path(rel)})
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def bash(self, cmd):
        out = self.hook("pre-bash", tool_name="Bash", tool_input={"command": cmd})
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def to_build(self, slug="feat-a"):
        self.cli("new", slug)
        self.prompt("sdlc approve intent")
        self.write("docs/sdlc/%s/spec.md" % slug, "# spec\n")
        self.prompt("sdlc approve spec")
        self.write("docs/sdlc/%s/plan.md" % slug, "# plan\n")
        self.prompt("sdlc approve plan")

    # -- tests
    def test_inert_without_config(self):
        os.remove(self.path("sdlc.config.json"))
        self.assertEqual(self.hook("prompt", prompt="hi"), {})
        self.assertEqual(self.edit("src/app.js"), "allow")
        code, out = self.cli("status")
        self.assertEqual(code, 1)
        self.assertIn("Only the user can enable it", out)

    def test_code_blocked_without_feature(self):
        self.assertIn("No active feature", self.prompt("hello"))
        self.assertEqual(self.edit("src/app.js"), "deny")
        self.assertEqual(self.edit("README.md"), "allow")
        self.assertEqual(self.edit("docs/notes.txt"), "allow")
        self.assertEqual(self.edit("/tmp/elsewhere.js"), "allow")

    def test_stage_chain(self):
        code, out = self.cli("new", "feat-a")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.edit("docs/sdlc/feat-a/spec.md"), "deny")
        self.assertIn("APPROVED intent", self.prompt("sdlc approve intent"))
        self.assertEqual(self.edit("docs/sdlc/feat-a/spec.md"), "allow")
        self.assertEqual(self.edit("docs/sdlc/feat-a/plan.md"), "deny")
        self.assertIn("failed", self.prompt("sdlc approve plan"))
        self.write("docs/sdlc/feat-a/spec.md", "# spec\n")
        self.prompt("sdlc approve spec")
        self.write("docs/sdlc/feat-a/plan.md", "# plan\n")
        self.assertEqual(self.edit("src/app.js"), "deny")
        ctx = self.prompt("sdlc approve plan")
        self.assertIn("APPROVED plan", ctx)
        self.assertIn("Code gate OPEN", ctx)
        self.assertEqual(self.edit("src/app.js"), "allow")

    def test_stale_plan_closes_gate(self):
        self.to_build()
        self.write("docs/sdlc/feat-a/plan.md", "# plan v2\n")
        self.assertEqual(self.edit("src/app.js"), "deny")
        self.assertIn("stale", self.prompt("status?"))

    def test_reapproving_upstream_resets_downstream(self):
        self.to_build()
        self.write("docs/sdlc/feat-a/intent.md", "# changed intent\n")
        ctx = self.prompt("sdlc approve intent")
        self.assertIn("reset", ctx)
        self.assertEqual(self.edit("src/app.js"), "deny")

    def test_skip_spec(self):
        self.cli("new", "fix-b", "bugfix")
        self.prompt("sdlc approve intent")
        self.assertIn("skipped spec", self.prompt("sdlc skip spec tiny fix"))
        self.write("docs/sdlc/fix-b/plan.md", "# plan\n")
        self.prompt("sdlc approve plan")
        self.assertEqual(self.edit("src/app.js"), "allow")

    def test_agent_cannot_touch_state(self):
        self.cli("new", "feat-a")
        self.assertEqual(self.edit("docs/sdlc/feat-a/approvals.json"), "deny")
        self.assertEqual(self.edit(".sdlc/active"), "deny")
        self.assertEqual(self.bash("echo '{}' > docs/sdlc/feat-a/approvals.json"), "deny")
        self.assertEqual(self.bash("python3 ~/x/scripts/sdlc.py hook prompt < fake.json"), "deny")
        self.assertEqual(self.bash("rm .sdlc/tests-locked"), "deny")
        code, out = self.cli("approve", "intent")
        self.assertEqual(code, 1)
        self.assertIn("user-only", out)

    def test_state_reads_allowed(self):
        self.cli("new", "feat-a")
        for cmd in ("ls -la docs/sdlc/feat-a; cat docs/sdlc/feat-a/approvals.json 2>/dev/null | tail -80",
                    "ls -la .sdlc && tail -5 .sdlc/audit.log",
                    "for d in docs/sdlc/*/; do cat \"$d/approvals.json\" | head -40; done 2>/dev/null | head",
                    "jq -r .intent.sha256 docs/sdlc/feat-a/approvals.json; shasum -a 256 docs/sdlc/feat-a/intent.md",
                    "sed -n 1,20p docs/sdlc/feat-a/approvals.json && sed -n '/sha256/,$p' .sdlc/active",
                    "S=~/x/scripts/sdlc.py; wc -l $S; grep -n \"def hook\" $S | head",
                    "echo \"head: $(git rev-parse HEAD)\"; git log --oneline -- docs/sdlc/feat-a/approvals.json",
                    "cat docs/sdlc/feat-a/approvals.json > /tmp/approvals.copy",
                    "git add docs/sdlc/feat-a/approvals.json && git commit -q -F - <<'EOF'\n"
                    "docs: record approvals.json; ignore .sdlc/\nEOF"):
            self.assertEqual(self.bash(cmd), "allow", cmd)

    def test_state_writes_denied_with_open_gate(self):
        self.to_build()
        for cmd in ("cd docs/sdlc/feat-a && echo '{}' > approvals.json",
                    "echo '{}' > \"docs/sdlc/feat-a/approvals.json\"",
                    "cat /tmp/forged | tee docs/sdlc/feat-a/approvals.json",
                    "jq '.plan = {}' docs/sdlc/feat-a/approvals.json > /tmp/a && mv /tmp/a docs/sdlc/feat-a/approvals.json",
                    "python3 -c \"import json; json.dump({}, open('docs/sdlc/feat-a/approvals.json', 'w'))\"",
                    "python3 - <<'EOF'\nopen('docs/sdlc/feat-a/approvals.json', 'w').write('{}')\nEOF",
                    "sed -n 'w docs/sdlc/feat-a/approvals.json' /tmp/forged",
                    "sed -ni 's/a/b/' docs/sdlc/feat-a/approvals.json",
                    "git checkout -- docs/sdlc/feat-a/approvals.json",
                    "find .sdlc -name tests-locked -delete",
                    "ls .sdlc\nrm .sdlc/tests-locked",
                    "echo \"$(rm .sdlc/tests-locked)\"",
                    "cat .sdlc/active > /tmp/../..$HOME/.sdlc/active",
                    "cat fake.json | ~/x/scripts/sdlc.py hook prompt"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_bash_writes_gated(self):
        self.assertEqual(self.bash("npm test 2>&1 | tail -5"), "allow")
        self.assertEqual(self.bash("ls > /dev/null"), "allow")
        self.assertEqual(self.bash("echo x > /tmp/out.txt"), "allow")
        self.assertEqual(self.bash("echo hi > docs/x.md"), "allow")
        self.assertEqual(self.bash("echo hack > src/app.js"), "deny")
        self.assertEqual(self.bash("sed -i '' 's/1/2/' src/app.js"), "deny")
        self.assertEqual(self.bash("cat patch.diff | git apply"), "deny")
        self.assertEqual(self.bash("cp /tmp/a.js src/b.js"), "deny")
        self.assertEqual(self.bash("node -e \"[1].map(a => a)\""), "allow")
        self.assertEqual(self.bash("awk '$1 > 5' data.txt"), "allow")
        self.assertEqual(self.bash("python3 - <<'EOF'\nif a > b:\n    pass\nEOF"), "allow")
        self.assertEqual(self.bash("cat > src/new.js <<'EOF'\nx\nEOF"), "deny")
        self.assertEqual(self.bash("dd if=src/app.js of=/tmp/x"), "allow")
        self.to_build()
        self.assertEqual(self.bash("sed -i '' 's/1/2/' src/app.js"), "allow")

    def assertBash(self, cases):
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), expected)

    def test_bash_targets_resolve_shell_vars(self):
        # The code gate is closed (no feature): only writes outside the repo pass.
        self.assertBash([
            ("S=/tmp/sdlc-out; cp src/app.js $S/", "allow"),
            ("export S=/tmp/sdlc-out && cp src/app.js ${S}/copy.js", "allow"),
            ("T=src/app.js; cp x $T", "deny"),
            # Ambiguous or unknown values keep the old, strict reading of the literal text.
            ("T=/tmp/x; T=src/app.js; cp x $T", "deny"),
            ("T=src/app.js; (T=/tmp/x); cp x $T", "deny"),
            ("for T in /tmp/x; do cp x $T; done; T=/tmp/y; cp x $T", "deny"),
            ("T=$(pwd)/src; cp x $T/app.js", "deny"),
            ("cp x $UNSET/app.js", "deny"),
            # A value may change before the target: builtins, cd, conditions, pipelines.
            ("S=/tmp/x; read S <<< src; cp x $S/app.js", "deny"),
            ("S=/tmp/x; printf -v S '%s' src; cp x $S/app.js", "deny"),
            ("S=/tmp/x; cd src; cp x $S/../app.js", "deny"),
            ("false && S=/tmp/x; cp x $S/app.js", "deny"),
            ("S=/tmp/x | cat; cp x $S/app.js", "deny"),
            ("S=/tmp/x; S+=/../..%s/src; cp x $S/app.js" % self.root, "deny"),
            # Single quotes never expand; double quotes do.
            ("S=/tmp/x; mkdir '$S'; cp x '$S/../src/app.js'", "deny"),
            ('S=/tmp/x; cp x "$S/out.js"', "allow"),
        ])

    def test_bash_targets_are_normalized_and_unquoted(self):
        self.assertBash([
            ("cp x /tmp/..%s/src/app.js" % self.root, "deny"),
            ("S=/tmp/..%s/src; cp x $S/app.js" % self.root, "deny"),
            ('echo x > "src/app.js"', "deny"),
            ("echo x > 'src/app.js'", "deny"),
            ('echo "a > b" > /tmp/sdlc-out', "allow"),
        ])

    # fix-sed-inplace-parsing: the code gate is closed (no feature); /tmp/x is outside the repo.
    def test_inplace_bsd_suffix(self):
        self.assertBash([
            # AC1: a BSD suffix of its own is neither the script nor a file
            ("sed -i '' 's/a/b/' /tmp/x", "allow"),
            ('sed -i "" \'s/a/b/\' /tmp/x', "allow"),
            ("sed -i .bak 's/a/b/' /tmp/x", "allow"),
            ("sed -i '' 's/a/b/' src/app.js", "deny"),
            ("sed -i .bak 's/a/b/' src/app.js", "deny"),
            # AC2: the edited file decides, here a protected one
            ("sed -i '' 's/a/b/' CLAUDE.md", "ask"),
            # AC8: no file left is an unknown target
            ("sed -i '' 's/a/b/'", "deny"),
        ])

    def test_inplace_gnu_and_perl_unchanged(self):
        # AC3
        self.assertBash([
            ("sed -i 's/a/b/' /tmp/x", "allow"),
            ("sed -i.bak 's/a/b/' /tmp/x", "allow"),
            ("perl -pi -e 's/a/b/' /tmp/x", "allow"),
            ("perl -i.bak -pe 's/a/b/' /tmp/x", "allow"),
            ("sed -i 's/a/b/' src/app.js", "deny"),
            ("sed -i.bak 's/a/b/' src/app.js", "deny"),
            ("perl -pi -e 's/a/b/' src/app.js", "deny"),
            ("perl -i.bak -pe 's/a/b/' src/app.js", "deny"),
        ])

    def test_inplace_long_and_capital_forms(self):
        self.assertBash([
            # AC4
            ("sed --in-place 's/a/b/' src/app.js", "deny"),
            ("sed --in-place=.bak 's/a/b/' src/app.js", "deny"),
            ("sed --in-place 's/a/b/' /tmp/x", "allow"),
            # AC5: -I is in-place for sed, an include path for perl
            ("sed -I '' 's/a/b/' src/app.js", "deny"),
            ("perl -I lib -e 'print 1'", "allow"),
        ])

    def test_inplace_script_options(self):
        # AC6: a script given by option leaves every positional a file
        self.assertBash([
            ("sed -i -f fix.sed src/app.js", "deny"),
            ("sed -i --expression=s/a/b/ src/app.js", "deny"),
        ])

    def test_inplace_values_and_double_dash(self):
        # AC7
        self.assertBash([
            ("sed -i -l 80 's/a/b/' /tmp/x", "allow"),
            ("sed -i -- 's/a/b/' /tmp/x", "allow"),
            ("sed -i -- 's/a/b/' src/app.js", "deny"),
        ])

    def test_inplace_review_findings(self):
        # A word read as suffix or script that names an existing file is still a target (NFR1).
        self.write(".eslintrc.js", "x\n")
        self.assertBash([
            # gsed is GNU: a suffix is never a word of its own
            ("gsed -e 's/a/b/' -i .eslintrc.js /tmp/x", "deny"),
            ("sed -e 's/a/b/' -i .eslintrc.js /tmp/x", "deny"),
            ("sed -e 's/a/b/' -i .bak /tmp/x", "allow"),
            ("sed -ni '' src/app.js /tmp/x", "deny"),
            # BSD -l takes no value; GNU -l takes digits
            ("sed -i '' -l 's/a/b/' src/app.js /tmp/x", "deny"),
            # GNU long options may be cut to a unique prefix
            ("sed --in-pl 's/a/b/' src/app.js", "deny"),
            ("sed --i=.bak 's/a/b/' src/app.js", "deny"),
            ("sed -i --expr=s/a/b/ src/app.js /tmp/x", "deny"),
            # a raw marker byte in the command must not crash the hook (fail open)
            ("sed -i s/a/b/ src/app.js \x019\x01", "deny"),
            # perl/ruby: -I and -M take values, so their letters are not flags
            ("perl -Ilib -e 'print 1'", "allow"),
            ("perl -Mstrict -e 'print 1'", "allow"),
            ("ruby -Ilib -e 'p 1'", "allow"),
            ("perl -Ilib -pi -e 's/a/b/' src/app.js", "deny"),
            # a quoted program name is still that program
            ("'sed' -i 's/a/b/' src/app.js", "deny"),
            ('"perl" -pi -e \'s/a/b/\' src/app.js', "deny"),
        ])

    def test_inplace_after_cd(self):
        # After an in-command cd the hook cannot stat words where the shell will: a word the
        # GNU reading edits stays a target.
        self.write("src/.b.js", "x\n")
        self.assertBash([
            ("cd src && sed -e 's/a/b/' -i .b.js /tmp/x", "deny"),
            ("pushd src && sed -e 's/a/b/' -i .b.js /tmp/x", "deny"),
            ("(cd src; sed -ni '' app.js /tmp/x)", "deny"),
            ("sed -e 's/a/b/' -i .bak /tmp/x", "allow"),
        ])

    def test_inplace_exists_only_for_simple_commands(self):
        # The hook checks a word against the disk only for one simple command and a word with
        # nothing for the shell to expand; anywhere else the word counts as a file.
        self.write(".eslintrc.js", "x\n")
        self.write("src/.b.js", "x\n")
        self.assertBash([
            # globs, braces and ~ expand to real files the hook cannot see in the text
            ("sed -e 's/a/b/' -i .eslint*.js /tmp/x", "deny"),
            ("sed -e 's/a/b/' -i .eslintrc.{js,x} /tmp/x", "deny"),
            ("sed -ni '' src/*.js /tmp/x", "deny"),
            # directory changes the hook cannot follow: quoted, zsh chdir, a later cd in a loop
            ("'cd' src && sed -e 's/a/b/' -i .b.js /tmp/x", "deny"),
            ("chdir src && sed -e 's/a/b/' -i .b.js /tmp/x", "deny"),
            ("for i in 1 2; do sed -e 's/a/b/' -i .b.js /tmp/x; cd src; done", "deny"),
            # one simple command with a plain word keeps the BSD reading
            ("sed -i '' 's/a/b/' /tmp/x", "allow"),
            ("sed -e 's/a/b/' -i .bak /tmp/x", "allow"),
        ])

    def test_inplace_operands_read_strictly(self):
        self.assertBash([
            # perl and ruby stop reading options at the first operand: the rest are files
            ("perl -pi -e 's/a/b/' /tmp/x -e src/app.js", "deny"),
            ("ruby -pi -e 'x' /tmp/x -e src/app.js", "deny"),
            # BSD sed stops there too; GNU sed reads options anywhere: both readings count
            ("sed -i '' 's/a/b/' /tmp/x -e src/app.js", "deny"),
            ("sed -i src/app.js -e 's/a/b/'", "deny"),
            ("sed -i 's/a/b/' /tmp/x -- src/app.js", "deny"),
        ])

    def test_inplace_option_forms(self):
        # FR1, FR3, FR5, FR6 forms the first tests left out
        self.assertBash([
            ("sed -i '~' 's/a/b/' /tmp/x", "allow"),
            ("sed --in-place=.bak 's/a/b/' /tmp/x", "allow"),
            ("sed -i --file fix.sed src/app.js", "deny"),
            ("sed -i --file=fix.sed src/app.js", "deny"),
            ("sed -i --expression 's/a/b/' src/app.js", "deny"),
            ("sed -i -ne 's/a/b/p' src/app.js", "deny"),
            ("sed -i --line-length=80 's/a/b/' /tmp/x", "allow"),
            ("sed -i --line-length 80 's/a/b/' /tmp/x", "allow"),
        ])

    def test_protected_and_deploy_ask(self):
        self.to_build()
        self.assertEqual(self.edit("CLAUDE.md"), "ask")
        self.assertEqual(self.edit("sdlc.config.json"), "ask")
        for rel in ("src/CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".claude/agents/x.md",
                    ".claude/skills/x/SKILL.md"):
            self.assertEqual(self.edit(rel), "ask", rel)
        self.assertEqual(self.edit(".claude/worktrees/w/src/app.js"), "allow")
        self.assertEqual(self.bash("git push -u origin main"), "ask")
        self.assertEqual(self.bash("git status"), "allow")

    def test_push_gate_reads_where_a_push_goes(self):
        sh(self.root, "git", "tag", "v1")
        sh(self.root, "git", "checkout", "-qb", "feat/a")
        # A plain update of an unprotected branch ships nothing: no approval needed.
        for cmd in ("git push", "git push -u origin HEAD", "git push origin feat/a",
                    "git push --set-upstream origin feat/a", "git push -o ci.skip origin feat/a",
                    "git push --dry-run origin HEAD:feat/b", "git push origin refs/heads/feat/a",
                    "git push origin @", "git push --recurse-submodules=check origin feat/a",
                    "git add -A && git commit -m 'wip' && git push -u origin HEAD"):
            self.assertEqual(self.bash(cmd), "allow", cmd)
        # Protected branches, force, delete, tags, mirror, and anything the hook cannot read ask.
        for cmd in ("git push origin main", "git push origin HEAD:main", "git push origin feat/a:master",
                    "git push origin release/1.0", "git push --force", "git push -f origin feat/a",
                    "git push -uf origin feat/a", "git push --force-with-lease origin feat/a",
                    "git push --forc origin feat/a", "git push origin +feat/a", "git push origin :feat/a",
                    "git push --delete origin feat/a", "git push -d origin feat/a", "git push --tags",
                    "git push --follow-tags", "git push --mirror", "git push --all", "git push origin v1",
                    "git push origin refs/tags/v2", "git push origin 'feat/*'", "git push origin $BRANCH",
                    "git -C ../other push origin feat/a", "git -c push.default=matching push",
                    "cd sub && git push", "bash -c 'git push origin feat/a'",
                    "git push origin feat/a && git push origin main",
                    "git push origin feat/a && npm publish"):
            self.assertEqual(self.bash(cmd), "ask", cmd)
        # The repository may change before the push runs, or the push may go elsewhere.
        for cmd in ("git checkout main && git push", "git switch main; git push -u origin HEAD",
                    "git tag v9 && git push origin v9", "npm test && git push origin feat/a",
                    "GIT_DIR=../x/.git git push",
                    "export GIT_WORK_TREE=../x; git push origin feat/a", "HOME=/tmp git push",
                    "git push origin feat/a & git push",
                    "git push --recurse-submodules=on-demand origin feat/a"):
            self.assertEqual(self.bash(cmd), "ask", cmd)
        sh(self.root, "git", "config", "remote.origin.push", "refs/heads/*:refs/heads/main")
        self.assertEqual(self.bash("git push"), "ask")
        self.assertEqual(self.bash("git push origin feat/a"), "allow")
        sh(self.root, "git", "config", "--unset", "remote.origin.push")
        sh(self.root, "git", "config", "push.default", "matching")
        self.assertEqual(self.bash("git push"), "ask")
        self.assertEqual(self.bash("git push origin feat/a"), "allow")
        # Protected branches come from the config; a deny decision denies them.
        self.write("sdlc.config.json", json.dumps({"verify": [], "protected_branches": ["feat/*"],
                                                   "gated_command_decision": "deny"}))
        self.assertEqual(self.bash("git push origin feat/a"), "deny")
        self.assertEqual(self.bash("git push origin main"), "allow")

    def test_push_on_a_protected_branch_asks(self):
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=self.root,
                                capture_output=True, text=True).stdout.strip()
        self.assertIn(branch, ("main", "master"))
        for cmd in ("git push", "git push -u origin HEAD", "git push origin @", "git push origin head"):
            self.assertEqual(self.bash(cmd), "ask", cmd)

    def plan_with_files(self, files):
        self.write("docs/sdlc/feat-a/plan.md", "# Plan\n\n## Files affected\n%s\n## Work sequence\n"
                   "1. [ ] `src/not-a-file-entry.js` in a step\n" % files)
        self.prompt("sdlc approve plan")

    def pre_edit_context(self, rel):
        out = self.hook("pre-edit", tool_name="Edit", tool_input={"file_path": self.path(rel)})
        spec = out.get("hookSpecificOutput", {})
        return spec.get("permissionDecision", "allow"), spec.get("additionalContext")

    def test_edit_outside_the_plan_gets_a_note(self):
        self.to_build()
        self.plan_with_files("| File | Change | Why |\n|------|--------|-----|\n"
                             "| `src/app.js` | modify | x |\n| src/lib/ | create | y |\n"
                             "| tests/*.test.js (new) | create | z |\n")
        for rel in ("src/app.js", "src/lib/util.js", "tests/app.test.js", "docs/notes.md"):
            self.assertEqual(self.pre_edit_context(rel), ("allow", None), rel)
        decision, note = self.pre_edit_context("src/other.js")
        self.assertEqual(decision, "allow")
        self.assertIn("src/other.js is not in *Files affected*", note)
        self.assertIn("sdlc approve plan", note)
        # Once per file and session; the step text is not a Files affected entry.
        self.assertEqual(self.pre_edit_context("src/other.js"), ("allow", None))
        self.assertIsNotNone(self.pre_edit_context("src/not-a-file-entry.js")[1])
        with open(self.path(".sdlc/audit.log")) as f:
            self.assertIn("off-plan", f.read())

    def test_plan_without_files_or_fasttrack_gets_no_note(self):
        self.to_build()
        self.plan_with_files("- `src/app.js`: the entry point\n")
        self.assertEqual(self.pre_edit_context("src/app.js"), ("allow", None))
        self.write("docs/sdlc/feat-a/plan.md", "# plan without the section\n")
        self.prompt("sdlc approve plan")
        self.assertEqual(self.pre_edit_context("src/other.js"), ("allow", None))
        self.plan_with_files("| `src/app.js` | modify | x |\n")
        self.prompt("sdlc trivial small fix")
        self.assertEqual(self.pre_edit_context("src/other.js"), ("allow", None))

    def raw_hook(self, name, **payload):
        data = {"session_id": SID, "cwd": self.root}
        data.update(payload)
        r = subprocess.run([sys.executable, SCRIPT, "hook", name], input=json.dumps(data),
                           capture_output=True, text=True, cwd=self.root,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def test_gates_fail_closed_on_a_hook_error(self):
        self.to_build()
        # Malformed input crashes the handler: the gates deny, and the person sees why.
        for name, tool_input in (("pre-edit", {"file_path": 123}), ("pre-bash", {"command": ["git", "push"]})):
            out = self.raw_hook(name, tool_name="X", tool_input=tool_input)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", name)
            self.assertIn("failed closed", out["systemMessage"])
        with open(self.path(".sdlc/audit.log")) as f:
            self.assertEqual(sum(json.loads(line)["event"] == "hook-error" for line in f), 2)
        # Hooks that gate nothing are skipped, with a note.
        out = self.raw_hook("prompt", prompt=123)
        self.assertNotIn("hookSpecificOutput", out)
        self.assertIn("skipped", out["systemMessage"])
        # The person can let calls through while the bug is fixed.
        self.write("sdlc.config.json", json.dumps({"verify": [], "on_hook_error": "allow"}))
        out = self.raw_hook("pre-edit", tool_name="X", tool_input={"file_path": 123})
        self.assertNotIn("hookSpecificOutput", out)
        self.assertIn("skipped", out["systemMessage"])

    def test_nested_session_cannot_send_user_commands(self):
        for cmd in ("claude -p \"sdlc approve plan\"",
                    "echo 'sdlc unlock tests' | claude -p",
                    "bash -c 'claude --print \"sdlc done\"'",
                    "npx @anthropic-ai/claude-code -p \"sdlc trivial\""):
            self.assertEqual(self.bash(cmd), "deny", cmd)
        for cmd in ("claude --version", "grep -rn \"sdlc approve\" docs",
                    "cat ~/.claude/settings.json"):
            self.assertEqual(self.bash(cmd), "allow", cmd)

    def test_config_template_matches_defaults(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        import sdlc
        with open(os.path.join(os.path.dirname(HERE), "skills", "init", "config-template.json")) as f:
            template = json.load(f)
        for key, value in sdlc.DEFAULTS.items():
            if key != "verify":
                self.assertEqual(template.get(key), value, key)

    def test_deploy_gate_ignores_mentions(self):
        self.write("sdlc.config.json", json.dumps({"verify": [], "gated_commands": GATED}))
        for cmd in ("cat -n tools/commands/upload-assets.ts | head -60",
                    "for f in src/upload-assets.ts src/app.js; do cat \"$f\"; done",
                    "cat ios/fastlane/Fastfile",
                    "python3 - <<'EOF'\ns = '''docker compose -f infra/dev/compose.yaml up -d'''\nEOF",
                    "python3 - <<'EOF'\nrow = 'via `docker-compose`; schema is up to date'\nEOF",
                    "git commit -q -F - <<'EOF'\ndocs: explain git push\nEOF",
                    "git commit -m \"$(cat <<'EOF'\nfeat: gate git push\nEOF\n)\"",
                    "grep -rn \"git push\" docs",
                    "node -e \"console.log('git push')\""):
            self.assertEqual(self.bash(cmd), "allow", cmd)

    def test_deploy_gate_catches_invocations(self):
        self.write("sdlc.config.json", json.dumps({"verify": [], "gated_commands": GATED}))
        for cmd in ("cd app && git push",
                    "/opt/homebrew/bin/docker-compose -f infra/dev/compose.yaml up -d",
                    "pnpm content upload-assets --slug demo",
                    "npx tsx tools/commands/upload-assets.ts",
                    "sudo /usr/local/bin/fastlane beta",
                    "bash -c 'cd app && git push'",
                    "ssh deploy@host \"git push\"",
                    "echo 'cd app && git push' | bash",
                    "bash <<'EOF'\ngit push\nEOF",
                    "echo \"$(git push)\"",
                    "cat > notes.md <<EOF\n`git push`\nEOF"):
            self.assertEqual(self.bash(cmd), "ask", cmd)

    def test_status_command_needs_no_turn(self):
        self.cli("new", "feat-a")
        out = self.hook("prompt", prompt="sdlc status")
        self.assertEqual(out["decision"], "block")
        self.assertIn("Feature `feat-a`", out["reason"])
        self.assertNotIn("hookSpecificOutput", out)

    def test_fasttrack_is_session_scoped(self):
        self.assertIn("fast-track", self.prompt("sdlc trivial fix typo"))
        self.assertEqual(self.edit("src/app.js"), "allow")
        other = self.hook("pre-edit", session_id="other", tool_name="Write",
                          tool_input={"file_path": self.path("src/app.js")})
        self.assertEqual(other["hookSpecificOutput"]["permissionDecision"], "deny")
        self.prompt("sdlc trivial off")
        self.assertEqual(self.edit("src/app.js"), "deny")

    def test_test_lock(self):
        self.to_build()
        self.assertEqual(self.edit("src/app.test.js"), "allow")
        self.cli("lock-tests")
        self.assertEqual(self.edit("src/app.test.js"), "deny")
        self.assertEqual(self.edit("tests/test_x.py"), "deny")
        self.assertEqual(self.edit("src/app.js"), "allow")
        self.prompt("sdlc unlock tests")
        self.assertEqual(self.edit("src/app.test.js"), "allow")

    def test_new_feature_auto_activates_on_intent_write(self):
        out = self.hook("pre-edit", tool_name="Write",
                        tool_input={"file_path": self.path("docs/sdlc/my-feat/intent.md")})
        self.assertIn("active feature", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("my-feat", self.prompt("status"))

    def test_done_closes_feature(self):
        self.to_build()
        self.assertIn("closed", self.prompt("sdlc done"))
        self.assertEqual(self.edit("src/app.js"), "deny")

    def test_stop_verification(self):
        self.prompt("start")  # records the baseline fingerprint
        self.assertEqual(self.hook("stop"), {})  # nothing changed
        self.to_build()
        self.write("src/app.js", "console.log(2)\n")
        self.write("FAIL", "x")
        out = self.hook("stop")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("test ! -f FAIL", out["reason"])
        os.remove(self.path("FAIL"))
        out = self.hook("stop")
        self.assertIn("passed", out.get("systemMessage", ""))
        self.assertEqual(self.hook("stop"), {})  # already verified this state

    def test_stop_gives_up_after_retries(self):
        self.prompt("start")
        self.to_build()
        self.write("src/app.js", "console.log(3)\n")
        self.write("FAIL", "x")
        self.assertEqual(self.hook("stop").get("decision"), "block")
        self.assertEqual(self.hook("stop").get("decision"), "block")
        out = self.hook("stop")
        self.assertNotIn("decision", out)
        self.assertIn("still failing", out.get("systemMessage", ""))

    def test_state_json(self):
        rc, out = self.cli("state", "--session", SID)
        self.assertEqual(rc, 0)
        state = json.loads(out)
        self.assertTrue(state["enabled"])
        self.assertIsNone(state["active"])
        self.assertEqual(state["code_gate"], "closed")
        self.cli("new", "feat-a")
        state = json.loads(self.cli("state")[1])
        self.assertEqual((state["active"], state["stage"], state["stage_state"]), ("feat-a", "intent", "draft"))
        # hooks/ui.tsx reads these keys; types/index.d.ts declares them (SdlcState, StageInfo)
        self.assertEqual(set(state), {"enabled", "root", "active", "stage", "stage_state", "stage_label",
                                      "next_skill", "code_gate", "artifact", "stages", "features",
                                      "tests_locked", "fasttrack", "verify", "review", "pr", "done"})
        self.assertEqual(set(state["stages"]["intent"]), {"state", "path", "sha256", "approved_sha256",
                                                          "by", "at", "reason"})
        self.assertEqual(state["artifact"], "docs/sdlc/feat-a/intent.md")
        self.assertEqual(state["stages"]["spec"]["state"], "missing")
        self.to_build()
        self.write("src/app.js", "console.log(2)\n")
        self.hook("stop")
        state = json.loads(self.cli("state", "--session", SID)[1])
        self.assertEqual((state["stage"], state["code_gate"]), ("build", "open"))
        self.assertEqual(state["stages"]["plan"]["by"], "Tester")
        self.assertTrue(state["verify"]["last"]["ok"])
        os.remove(self.path("sdlc.config.json"))
        self.assertEqual(json.loads(self.cli("state")[1]), {"enabled": False})

    def review(self, text, agent="sdlc:sdlc-reviewer"):
        self.hook("subagent-stop", agent_type=agent, last_assistant_message=text)
        return json.loads(self.cli("state", "--session", SID)[1])["review"]

    def test_review_verdict_follows_the_change(self):
        sh(self.root, "git", "checkout", "-qb", "feat/a")
        self.to_build()
        self.write("src/app.js", "console.log(2)\n")
        self.assertIsNone(self.review("READY FOR HUMAN REVIEW", agent="Explore"))
        self.assertIsNone(self.review("no verdict line"))
        self.assertEqual(self.review("[Major] src/app.js:1 - x\nCHANGES REQUIRED")["verdict"], "changes")
        # A line that names both verdicts reads as changes required.
        self.assertEqual(self.review("Verdict: CHANGES REQUIRED, not READY FOR HUMAN REVIEW")["verdict"],
                         "changes")
        review = self.review("AC coverage ...\nREADY FOR HUMAN REVIEW")
        self.assertEqual((review["verdict"], review["current"]), ("ready", True))
        # Committing on the feature branch keeps the review; a docs change does too.
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-qm", "work")
        self.write("docs/notes.md", "notes\n")
        self.assertTrue(json.loads(self.cli("state")[1])["review"]["current"])
        # Any code change after the review makes it stale.
        self.write("src/app.js", "console.log(3)\n")
        self.assertFalse(json.loads(self.cli("state")[1])["review"]["current"])

    def test_review_from_subagent_handback(self):
        self.to_build()
        self.hook("post-tool", tool_name="SubagentHandback", agent_type="sdlc:sdlc-reviewer",
                  tool_input={"message": "READY FOR HUMAN REVIEW"}, tool_response={})
        self.assertEqual(json.loads(self.cli("state")[1])["review"]["verdict"], "ready")

    def test_pr_recorded_from_create_only(self):
        self.to_build()
        pr = lambda: json.loads(self.cli("state")[1])["pr"]
        self.hook("post-tool", tool_name="Bash", tool_input={"command": "gh pr view 7"},
                  tool_response={"stdout": "https://github.com/o/r/pull/7\n"})
        self.hook("post-tool", tool_name="mcp__GitLab__get_merge_request", tool_input={},
                  tool_response={"web_url": "https://gitlab.example.com/g/r/-/merge_requests/3"})
        self.assertIsNone(pr())
        self.hook("post-tool", tool_name="Bash", tool_input={"command": "git push -u origin HEAD && gh pr create --fill"},
                  tool_response={"stdout": "https://github.com/o/r/pull/12\n", "stderr": ""})
        self.assertEqual(pr()["url"], "https://github.com/o/r/pull/12")
        self.hook("post-tool", tool_name="mcp__GitLab__save_merge_request", tool_input={},
                  tool_response=[{"type": "text", "text": "{\"web_url\":\"https://gitlab.example.com/g/r/-/merge_requests/4\"}"}])
        self.assertEqual(pr()["url"], "https://gitlab.example.com/g/r/-/merge_requests/4")

    def test_exit_plan_mode_points_to_plan_md(self):
        self.cli("new", "feat-a")
        self.prompt("sdlc approve intent")
        self.prompt("sdlc skip spec small")
        out = self.hook("post-tool", tool_name="ExitPlanMode", tool_input={"plan": "# Plan"},
                        tool_response={"plan": "# Plan", "filePath": "/tmp/p.md"})
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("docs/sdlc/feat-a/plan.md", context)
        self.assertIn("sdlc approve plan", context)
        self.assertEqual(self.edit("src/app.js"), "deny")

    def test_audit_log(self):
        self.edit("src/app.js")
        self.prompt("sdlc trivial")
        with open(self.path(".sdlc/audit.log")) as f:
            events = [json.loads(line)["event"] for line in f]
        self.assertIn("gate-deny", events)
        self.assertIn("fasttrack", events)


if __name__ == "__main__":
    unittest.main()
