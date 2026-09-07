import unittest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orders import place_order


class TestOrders(unittest.TestCase):
    def test_order_ok(self):
        self.assertEqual(place_order(10, 4), {"ok": True, "reason": None, "remaining": 6})

    def test_order_out_of_stock(self):
        r = place_order(2, 5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "out_of_stock")
        self.assertEqual(r["remaining"], 2)


if __name__ == "__main__":
    unittest.main()
