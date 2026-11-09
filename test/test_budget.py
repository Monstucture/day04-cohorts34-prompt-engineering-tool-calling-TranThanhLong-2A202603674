"""Test calculate_budget_per_night: python -m unittest discover -s test -p test_budget.py"""

import unittest

from tools import calculate_budget_per_night as calc, execute_tool


class BudgetPerNight(unittest.TestCase):
    def test_splits_remaining_by_night_and_traveler(self):
        data = calc(10000000, 4, 4300000, " Đà Nẵng ", 2)["data"]
        self.assertEqual(data["destination"], "Đà Nẵng")
        self.assertEqual(data["remaining_budget"], 5700000)
        self.assertEqual(data["remaining_per_night"], 1425000)
        self.assertEqual(data["remaining_per_night_per_traveler"], 712500)
        self.assertTrue(data["within_budget"])

    def test_flights_over_budget_are_reported(self):
        data = calc(1000000, 2, 1600000, "DAD", 1)["data"]
        self.assertEqual(data["remaining_per_night"], -300000)
        self.assertFalse(data["within_budget"])

    def test_exact_budget_is_still_within_budget(self):
        data = calc(1000000, 2, 1000000, "DAD", 1)["data"]
        self.assertEqual(data["remaining_per_night"], 0)
        self.assertTrue(data["within_budget"])

    def test_model_float_counts_work_through_registry(self):
        result = execute_tool("calculate_budget_per_night", {
            "total_budget": 900, "nights": 3.0, "cheapest_flights_total": 0,
            "destination": "SGN", "travelers": 1.0,
        })
        self.assertEqual(result["data"]["remaining_per_night"], 300)

    def test_invalid_arguments(self):
        for args in [
            (0, 2, 100, "DAD", 1),
            ("1000", 2, 100, "DAD", 1),
            (float("inf"), 2, 100, "DAD", 1),
            (True, 2, 100, "DAD", 1),
            (1000, 2, -1, "DAD", 1),
            (1000, 0, 100, "DAD", 1),
            (1000, 1.5, 100, "DAD", 1),
            (1000, 2, 100, "  ", 1),
            (1000, 2, 100, "DAD", 0),
        ]:
            self.assertEqual(calc(*args)["code"], "INVALID_ARGUMENT", args)


if __name__ == "__main__":
    unittest.main()
