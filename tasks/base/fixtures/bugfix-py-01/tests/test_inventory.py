import unittest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inventory import reserve, restock, OutOfStockError


class TestReserve(unittest.TestCase):
    def test_reserve_partial(self):
        self.assertEqual(reserve(10, 3), 7)

    def test_reserve_exact_depletion(self):
        # An order that exactly depletes the stock must be allowed (0 remaining)
        self.assertEqual(reserve(5, 5), 0)

    def test_reserve_insufficient(self):
        with self.assertRaises(OutOfStockError):
            reserve(3, 4)

    def test_reserve_invalid_qty(self):
        with self.assertRaises(ValueError):
            reserve(5, 0)


class TestRestock(unittest.TestCase):
    def test_restock(self):
        self.assertEqual(restock(3, 2), 5)


if __name__ == "__main__":
    unittest.main()
