"""todo.py CLI 인수 테스트 — 구현 대상 스펙의 정본.

repo 루트의 todo.py 를 임시 작업 디렉토리에서 subprocess 로 실행한다.
todos.json 은 '현재 작업 디렉토리' 기준이어야 한다.
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
        r = run(["add", "장보기"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "추가됨: #1 장보기")
        run(["add", "빨래"], self.dir)
        r = run(["list"], self.dir)
        self.assertEqual(r.stdout.strip().splitlines(), ["#1 [ ] 장보기", "#2 [ ] 빨래"])

    def test_done(self):
        run(["add", "a"], self.dir)
        run(["add", "b"], self.dir)
        r = run(["done", "1"], self.dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "완료: #1")
        r = run(["list"], self.dir)
        self.assertEqual(r.stdout.strip().splitlines(), ["#1 [x] a", "#2 [ ] b"])

    def test_done_missing_id(self):
        r = run(["done", "99"], self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "오류: 없는 id")

    def test_rm(self):
        run(["add", "a"], self.dir)
        r = run(["rm", "1"], self.dir)
        self.assertEqual(r.stdout.strip(), "삭제: #1")
        r = run(["list"], self.dir)
        self.assertEqual(r.stdout.strip(), "")

    def test_rm_missing_id(self):
        r = run(["rm", "7"], self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "오류: 없는 id")

    def test_id_not_reused(self):
        run(["add", "a"], self.dir)
        run(["add", "b"], self.dir)
        run(["rm", "2"], self.dir)
        r = run(["add", "c"], self.dir)
        self.assertEqual(r.stdout.strip(), "추가됨: #3 c")

    def test_corrupted_store(self):
        with open(os.path.join(self.dir, "todos.json"), "w") as f:
            f.write("{ this is not json")
        r = run(["list"], self.dir)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "오류: 저장 파일 손상")


if __name__ == "__main__":
    unittest.main()
