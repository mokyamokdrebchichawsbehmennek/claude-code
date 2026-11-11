"""Run with: python3 -m unittest discover plugins/foresight/tests"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import foresight as fs  # noqa: E402

CLI = [sys.executable, str(ROOT / "scripts" / "foresight.py")]


class PredictionParsing(unittest.TestCase):
    def test_split_keeps_command_and_extracts_annotation(self):
        bare, raw = fs.split_prediction("npm test  # predict: exit=0 p=0.8")
        self.assertEqual(bare, "npm test")
        self.assertEqual(raw, "exit=0 p=0.8")

    def test_only_last_line_is_annotated(self):
        bare, raw = fs.split_prediction("echo '# predict: exit=1'\nmake")
        self.assertIsNone(raw)
        self.assertIn("make", bare)

    def test_parse_fields(self):
        self.assertEqual(fs.parse_prediction("exit=!0 p=70 out~/No such file/"),
                         {"exit": "!0", "p": 0.7, "out": "No such file"})
        self.assertEqual(fs.parse_prediction("exit=2"), {"exit": 2})

    def test_uncheckable_prediction_is_rejected(self):
        self.assertIsNone(fs.parse_prediction("p=0.9"))
        self.assertIsNone(fs.parse_prediction("looks fine"))

    def test_score(self):
        self.assertTrue(fs.score({"exit": 0, "out": r"\d+ passed"}, 0, "12 passed")[0])
        self.assertFalse(fs.score({"exit": 0}, 1, "")[0])
        self.assertTrue(fs.score({"exit": "!0"}, None, "timed out")[0])
        hit, reasons = fs.score({"exit": 0, "out": "passed"}, 0, "3 failed")
        self.assertFalse(hit)
        self.assertEqual(len(reasons), 1)

    def test_signature(self):
        self.assertEqual(fs.signature("CI=1 npm test -- --watch"), "npm test")
        self.assertEqual(fs.signature("/usr/bin/pytest -q"), "pytest")


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "state"
        self.env = {**os.environ, "FORESIGHT_HOME": str(self.home)}
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def cli(self, *args, stdin=None):
        return subprocess.run([*CLI, *args], env=self.env, input=stdin,
                              capture_output=True, text=True)

    def hook(self, **event):
        out = self.cli("hook", stdin=json.dumps({"session_id": "s1", "cwd": self.tmp.name, **event}))
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout else None

    def bash(self, command, tool_use_id, exit_code=0, output="", session="s1"):
        """Replay one Bash call through the hooks; returns (pre, post) context."""
        common = {"tool_name": "Bash", "tool_input": {"command": command},
                  "tool_use_id": tool_use_id, "session_id": session}
        pre = self.hook(hook_event_name="PreToolUse", **common)
        if exit_code == 0:
            post = self.hook(hook_event_name="PostToolUse",
                             tool_response={"stdout": output, "stderr": ""}, **common)
        else:
            post = self.hook(hook_event_name="PostToolUseFailure",
                             error=f"Exit code {exit_code}\n{output}", **common)
        return pre, post

    def ledger(self):
        return fs.Store.read_jsonl(self.home / "ledger.jsonl")

    def script(self, name, body):
        path = Path(self.tmp.name) / name
        path.write_text(body)
        return str(path)


class WorldModel(Sandbox):
    def test_correct_prediction_is_silent_and_recorded(self):
        _, post = self.bash("make  # predict: exit=0 p=0.9", "t1")
        self.assertIsNone(post)
        (row,) = self.ledger()
        self.assertTrue(row["hit"])
        self.assertEqual(row["command"], "make")

    def test_wrong_prediction_is_fed_back(self):
        _, post = self.bash("make  # predict: exit=0 p=0.9", "t1", exit_code=2, output="boom")
        self.assertIn("expected exit=0, got exit=2", post)
        self.assertIn("foresight lesson add", post)
        self.assertFalse(self.ledger()[0]["hit"])

    def test_unpredicted_command_counts_toward_coverage_only(self):
        self.bash("ls", "t1")
        self.assertNotIn("hit", self.ledger()[0])

    def test_background_command_is_not_scored(self):
        common = {"tool_name": "Bash", "tool_use_id": "t1",
                  "tool_input": {"command": "serve  # predict: exit=0", "run_in_background": True}}
        self.hook(hook_event_name="PreToolUse", **common)
        self.hook(hook_event_name="PostToolUse", tool_response={"stdout": ""}, **common)
        self.assertNotIn("hit", self.ledger()[0])

    def test_lesson_resurfaces_once_before_a_matching_command(self):
        self.cli("lesson", "add", "--pattern", r"^npm test", "tests need the db container")
        self.hook(hook_event_name="SessionStart", session_id="s2")  # marks it shown in s2
        pre, _ = self.bash("npm test", "t1")
        self.assertIn("db container", pre)
        pre_again, _ = self.bash("npm test", "t2")
        self.assertIsNone(pre_again)
        pre_other, _ = self.bash("npm run build", "t3")
        self.assertIsNone(pre_other)
        pre_s2, _ = self.bash("npm test", "t4", session="s2")
        self.assertIsNone(pre_s2)

    def test_session_start_carries_lessons_and_tools(self):
        self.cli("lesson", "add", "the linter rewrites files in place")
        tool = self.script("hello.sh", "#!/bin/sh\necho hi\n")
        self.cli("tool", "add", "hello", "--desc", "say hi", "--file", tool, "--test", '"$TOOL"')
        context = self.hook(hook_event_name="SessionStart")
        self.assertIn("# predict:", context)
        self.assertIn("the linter rewrites files in place", context)
        self.assertIn("`hello`: say hi", context)

    def test_malformed_hook_input_never_fails_the_session(self):
        self.assertEqual(self.cli("hook", stdin="not json").returncode, 0)

    def test_stats(self):
        self.bash("make  # predict: exit=0 p=0.9", "t1")
        self.bash("make  # predict: exit=0 p=0.9", "t2", exit_code=1)
        self.bash("ls", "t3")
        stats = json.loads(self.cli("stats", "--json").stdout)
        self.assertEqual((stats["commands"], stats["predicted"]), (3, 2))
        self.assertEqual(stats["accuracy"], 0.5)
        self.assertAlmostEqual(stats["brier"], (0.01 + 0.81) / 2)
        self.assertEqual(stats["surprising_families"][0]["sig"], "make")
        self.assertEqual(self.cli("stats").returncode, 0)

    def test_suggest_finds_repeated_procedures(self):
        for i in range(3):
            self.bash("git fetch origin && git rebase origin/main", f"t{i}")
        self.assertIn("3x", self.cli("suggest").stdout)


class Toolbox(Sandbox):
    def add(self, name, body, test, *extra):
        return self.cli("tool", "add", name, "--desc", f"{name} tool",
                        "--file", self.script(f"{name}.sh", body), "--test", test, *extra)

    def test_tool_is_registered_only_if_its_test_passes(self):
        bad = self.add("double", "#!/bin/sh\necho $(( $1 + 2 ))\n", '[ "$("$TOOL" 4)" = 8 ]')
        self.assertEqual(bad.returncode, 1)
        self.assertIn("REJECTED", bad.stderr)
        self.assertEqual(fs.Store(self.home).load_index(), {})
        self.assertEqual(os.listdir(self.home / "tools"), [])

        good = self.add("double", "#!/bin/sh\necho $(( $1 * 2 ))\n", '[ "$("$TOOL" 4)" = 8 ]')
        self.assertEqual(good.returncode, 0, good.stderr)
        run = self.cli("tool", "run", "double", "21")
        self.assertEqual(run.stdout.strip(), "42")
        self.assertEqual(fs.Store(self.home).load_index()["double"]["uses"], 1)

    def test_tools_compose_and_depth_is_tracked(self):
        self.add("double", "#!/bin/sh\necho $(( $1 * 2 ))\n", '[ "$("$TOOL" 4)" = 8 ]')
        quad = self.add("quad", '#!/bin/sh\nforesight tool run double "$(foresight tool run double "$1")"\n',
                        '[ "$("$TOOL" 3)" = 12 ]')
        self.assertEqual(quad.returncode, 0, quad.stderr)
        index = fs.Store(self.home).load_index()
        self.assertEqual(index["quad"]["deps"], ["double"])
        self.assertEqual(fs.composition_depth(index, "quad"), 2)
        self.assertEqual(self.cli("tool", "rm", "double").returncode, 1)  # still a dependency

    def test_composing_an_unknown_tool_is_refused(self):
        out = self.add("quad", "#!/bin/sh\nforesight tool run nope 1\n", "true")
        self.assertEqual(out.returncode, 1)
        self.assertIn("unknown tools", out.stderr)

    def test_verify_flags_a_tool_that_stopped_working(self):
        marker = Path(self.tmp.name) / "dep"
        marker.write_text("x")
        self.add("needs-dep", "#!/bin/sh\ncat \"$1\"\n", f'"$TOOL" {marker}')
        self.assertEqual(self.cli("tool", "verify").returncode, 0)
        marker.unlink()
        out = self.cli("tool", "verify")
        self.assertEqual(out.returncode, 1)
        self.assertIn("BROKEN needs-dep", out.stdout)
        self.assertEqual(fs.Store(self.home).load_index()["needs-dep"]["status"], "broken")

    def test_names_cannot_escape_the_toolbox(self):
        out = self.cli("tool", "add", "../evil", "--desc", "x", "--file", self.script("e", "x"), "--test", "true")
        self.assertEqual(out.returncode, 2)

    def test_replacing_needs_force_and_search_finds_by_description(self):
        self.add("double", "#!/bin/sh\necho $(( $1 * 2 ))\n", "true")
        self.assertEqual(self.add("double", "#!/bin/sh\necho 0\n", "true").returncode, 1)
        self.assertEqual(self.add("double", "#!/bin/sh\necho 0\n", "true", "--force").returncode, 0)
        self.assertIn("double", self.cli("tool", "search", "DOUBLE").stdout)


if __name__ == "__main__":
    unittest.main()
