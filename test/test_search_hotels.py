"""Test search_hotels không cần mạng: python -m unittest discover -s test -p test_search_hotels.py"""

import json
import os
import unittest
from datetime import date, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from tools import execute_tool, search_hotels

IN_DATE = (date.today() + timedelta(days=5)).isoformat()
OUT_DATE = (date.today() + timedelta(days=8)).isoformat()


def prop(name, price):
    rate = {"extracted_lowest": price} if price is not None else {}
    return {"name": name, "rate_per_night": rate, "overall_rating": 4.2, "amenities": ["Wi-Fi"]}


def respond(body, seen=None):
    def opener(url, timeout):
        if seen is not None:
            seen.update(parse_qs(urlparse(url).query))
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(body).encode()
        return response
    return opener


def offline():
    env = {k: v for k, v in os.environ.items() if k != "SERPAPI_API_KEY"}
    return mock.patch.dict(os.environ, env, clear=True)


class RealApi(unittest.TestCase):
    def run_real(self, body, seen=None):
        with mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "x"}), \
                mock.patch("urllib.request.urlopen", respond(body, seen)):
            return search_hotels("Đà Nẵng", 1000000.9, ["hồ bơi"], IN_DATE, OUT_DATE)

    def test_sends_query_and_dates(self):
        seen = {}
        self.run_real({"properties": [prop("A", 500000)]}, seen)
        self.assertEqual(seen["q"], ["khách sạn Đà Nẵng hồ bơi"])
        self.assertEqual(seen["check_in_date"], [IN_DATE])
        self.assertEqual(seen["check_out_date"], [OUT_DATE])
        self.assertEqual(seen["max_price"], ["1000000"])

    def test_keeps_only_priced_hotels_in_budget_cheapest_first(self):
        body = {"properties": [prop("Dat", 2000000), prop("Khong gia", None), prop("B", 900000), prop("A", 500000)]}
        hotels = self.run_real(body)["data"]["hotels"]
        self.assertEqual([h["name"] for h in hotels], ["A", "B"])

    def test_no_results_is_not_upstream_error(self):
        result = self.run_real({"error": "Google Hotels hasn't returned any results for this query."})
        self.assertEqual(result["code"], "NO_HOTELS")

    def test_upstream_error_hides_key(self):
        with mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "secret"}), \
                mock.patch("urllib.request.urlopen", side_effect=TimeoutError("secret")):
            result = search_hotels("Đà Nẵng", 1000000)
        self.assertEqual(result["code"], "UPSTREAM_ERROR")
        self.assertNotIn("secret", result["message"])


class Offline(unittest.TestCase):
    def test_filters_budget_and_preferences(self):
        with offline():
            result = execute_tool("search_hotels", {
                "city": "da nang", "max_price_per_night": 1425000, "preferences": ["Hồ bơi"],
            })
        self.assertEqual([h["name"] for h in result["data"]["hotels"]], ["Khách sạn Mẫu Mỹ Khê"])

    def test_default_dates_are_returned(self):
        with offline():
            data = search_hotels("Hà Nội", 1000000)["data"]
        self.assertEqual(data["check_in_date"], (date.today() + timedelta(days=1)).isoformat())
        self.assertEqual(data["check_out_date"], (date.today() + timedelta(days=2)).isoformat())

    def test_nothing_in_budget(self):
        with offline():
            self.assertEqual(search_hotels("Hà Nội", 1000)["code"], "NO_HOTELS")

    def test_invalid_arguments(self):
        for args in [
            ("  ", 1000),
            ("Huế", 0),
            ("Huế", -300000),
            ("Huế", "1000"),
            ("Huế", 1000, "hồ bơi"),
            ("Huế", 1000, [""]),
            ("Huế", 1000, None, "20/12/2026"),
            ("Huế", 1000, None, "2000-01-01"),
            ("Huế", 1000, None, IN_DATE, IN_DATE),
        ]:
            self.assertEqual(search_hotels(*args)["code"], "INVALID_ARGUMENT", args)


if __name__ == "__main__":
    unittest.main()
