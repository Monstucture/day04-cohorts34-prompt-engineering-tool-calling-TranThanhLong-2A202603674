"""Test search_flights không cần mạng: python -m unittest discover -s test -p test_search_flights.py"""

import http.client
import json
import os
import unittest
from datetime import date, timedelta
from unittest import mock

from tools import execute_tool, search_flights

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
SERP = {
    "best_flights": [{
        "price": 3000000,
        "total_duration": 130,
        "flights": [{
            "airline": "Vietnam Airlines",
            "flight_number": "VN 213",
            "departure_airport": {"id": "HAN", "time": f"{TOMORROW} 06:00"},
            "arrival_airport": {"id": "SGN", "time": f"{TOMORROW} 08:10"},
        }],
    }]
}


def fake_urlopen(airlabs_dep_time):
    def opener(url, timeout):
        body = SERP if "serpapi" in url else {"response": {
            "dep_time": airlabs_dep_time, "dep_terminal": "T1", "dep_gate": "12",
            "arr_terminal": "T2", "arr_gate": "5", "arr_estimated": f"{TOMORROW} 08:25",
        }}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(body).encode()
        return response
    return opener


def json_response(body):
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(body).encode()
    return response


class SearchFlights(unittest.TestCase):
    def real(self, dep_time):
        env = {"SERPAPI_API_KEY": "x", "AIRLABS_API_KEY": "y"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", fake_urlopen(dep_time)):
            return search_flights("han", "sgn", TOMORROW, 2)

    def test_real_merges_gate(self):
        result = self.real(f"{TOMORROW} 06:00")
        segment = result["data"]["flights"][0]["segments"][0]
        self.assertEqual(result["data"]["flights"][0]["total_price"], 3000000)
        self.assertEqual((segment["airline"], segment["flight_number"]), ("Vietnam Airlines", "VN 213"))
        self.assertEqual(segment["departure"]["gate"], "12")
        self.assertEqual(segment["arrival"]["gate"], "5")
        self.assertEqual(segment["arrival"]["estimated_time"], f"{TOMORROW} 08:25")

    def test_gate_from_other_day_is_ignored(self):
        segment = self.real("2000-01-01 06:00")["data"]["flights"][0]["segments"][0]
        self.assertIsNone(segment["departure"]["gate"])
        self.assertEqual(segment["arrival"]["estimated_time"], f"{TOMORROW} 08:10")

    def test_upstream_error_never_returns_fake_data_or_key(self):
        with mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "secret"}), \
                mock.patch("urllib.request.urlopen", side_effect=TimeoutError("secret")):
            result = search_flights("HAN", "SGN", TOMORROW, 1)
        self.assertEqual(result["code"], "UPSTREAM_ERROR")
        self.assertNotIn("secret", result["message"])

    def with_urlopen(self, opener):
        env = {"SERPAPI_API_KEY": "x", "AIRLABS_API_KEY": "y"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", opener):
            return search_flights("HAN", "SGN", TOMORROW, 1)

    def test_closed_connection_during_gate_lookup_keeps_fares(self):
        def opener(url, timeout):
            if "serpapi" in url:
                return json_response(SERP)
            raise http.client.RemoteDisconnected("closed")
        result = self.with_urlopen(opener)
        self.assertEqual(result["status"], "success")
        self.assertIsNone(result["data"]["flights"][0]["segments"][0]["departure"]["gate"])

    def test_closed_connection_on_fare_search_is_upstream_error(self):
        result = self.with_urlopen(mock.Mock(side_effect=http.client.RemoteDisconnected("closed")))
        self.assertEqual(result["code"], "UPSTREAM_ERROR")

    def test_flat_airlabs_response_is_read(self):
        flat = {"dep_time": f"{TOMORROW} 06:00", "dep_gate": "12"}
        result = self.with_urlopen(lambda url, timeout: json_response(SERP if "serpapi" in url else flat))
        self.assertEqual(result["data"]["flights"][0]["segments"][0]["departure"]["gate"], "12")

    def test_gate_lookup_is_limited_to_ten_cheapest(self):
        leg = SERP["best_flights"][0]["flights"]
        priced = [{"price": 1000 - i, "flights": leg} for i in range(12)]
        body = {"best_flights": [{"flights": leg}, *priced]}
        calls = []

        def opener(url, timeout):
            calls.append(url)
            return json_response(body if "serpapi" in url else {})
        flights = self.with_urlopen(opener)["data"]["flights"]
        self.assertEqual([f["total_price"] for f in flights], list(range(989, 999)))
        self.assertEqual(len(calls), 11)

    def test_no_results_is_not_upstream_error(self):
        body = {"error": "Google Flights hasn't returned any results for this query."}
        self.assertEqual(self.with_urlopen(lambda url, timeout: json_response(body))["code"], "NO_FLIGHTS")

    def test_offline_multiplies_price(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("SERPAPI_API_KEY", None)
            result = execute_tool("search_flights", {
                "origin": "HAN", "destination": "SGN", "departure_date": TOMORROW, "travelers": 2.0,
            })
        self.assertEqual(result["data"]["flights"][0]["total_price"], 4300000)

    def test_invalid_arguments(self):
        for args in [
            ("HN", "SGN", TOMORROW, 1),
            ("HAN", "HAN", TOMORROW, 1),
            ("HAN", "SGN", "20/12/2026", 1),
            ("HAN", "SGN", "2000-01-01", 1),
            ("HAN", "SGN", TOMORROW, 0),
            ("HAN", "SGN", TOMORROW, 1.5),
        ]:
            self.assertEqual(search_flights(*args)["code"], "INVALID_ARGUMENT", args)


if __name__ == "__main__":
    unittest.main()
