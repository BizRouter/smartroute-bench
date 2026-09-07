import unittest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import generate_csv_report, generate_tsv_report, generate_pipe_report

HEADER = ["name", "qty", "memo"]
ROWS = [("apple", 3, ""), ("banana", 5, "fresh")]


class TestReports(unittest.TestCase):
    def test_csv(self):
        self.assertEqual(
            generate_csv_report(HEADER, ROWS),
            "name,qty,memo\napple,3,-\nbanana,5,fresh\n",
        )

    def test_tsv(self):
        self.assertEqual(
            generate_tsv_report(HEADER, ROWS),
            "name\tqty\tmemo\napple\t3\t-\nbanana\t5\tfresh\n",
        )

    def test_pipe(self):
        self.assertEqual(
            generate_pipe_report(HEADER, ROWS),
            "name | qty | memo\napple | 3 | -\nbanana | 5 | fresh\n",
        )

    def test_empty_rows(self):
        self.assertEqual(generate_csv_report(HEADER, []), "name,qty,memo\n")


if __name__ == "__main__":
    unittest.main()
