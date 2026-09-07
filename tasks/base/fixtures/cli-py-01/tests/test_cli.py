"""Acceptance tests for the todo.py CLI — the source of truth for the spec.

Runs todo.py from the repo root via subprocess in a temporary working directory.
todos.json must be resolved relative to the current working directory.

Note: the CLI's user-facing messages are Korean literals; the exact strings
asserted below are part of the spec.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODO = os.path.join(ROOT, "todo.py")


def run(args, cwd):
    return subprocess.run(
        [sys.executable, TODO] + args,
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )


class TestTodoCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="todo-test-")

    def test_add_and_list(self):
        r = run(["add", "groceries"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "added: #1 groceries")
        run(["add", "laundry"], self.dir)
        r = run(["list"], self.dir)
        self.assertEqual(r.stdout.strip().splitlines(), ["#1 [ ] groceries", "#2 [ ] laundry"])

    def test_done(self):
        run(["add", "a"], self.dir)
        run(["add", "b"], self.dir)
        r = run(["done", "1"], self.dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "done: #1")
        r = run(["list"], self.dir)
        self.assertEqual(r.stdout.strip().splitlines(), ["#1 [x] a", "#2 [ ] b"])

    def test_done_missing_id(self):
        r = run(["done", "99"], self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "error: no such id")

    def test_rm(self):
        run(["add", "a"], self.dir)
        r = run(["rm", "1"], self.dir)
        self.assertEqual(r.stdout.strip(), "removed: #1")
        r = run(["list"], self.dir)
        self.assertEqual(r.stdout.strip(), "")

    def test_rm_missing_id(self):
        r = run(["rm", "7"], self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "error: no such id")

    def test_id_not_reused(self):
        run(["add", "a"], self.dir)
        run(["add", "b"], self.dir)
        run(["rm", "2"], self.dir)
        r = run(["add", "c"], self.dir)
        self.assertEqual(r.stdout.strip(), "added: #3 c")

    def test_corrupted_store(self):
        with open(os.path.join(self.dir, "todos.json"), "w") as f:
            f.write("{ this is not json")
        r = run(["list"], self.dir)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "error: corrupt save file")


if __name__ == "__main__":
    unittest.main()
