"""Test server demo không cần Gemini: python -m unittest discover -s test -p test_travel_buddy_demo.py"""

import http.client
import json
import logging
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import travel_buddy_demo as demo


class FakeBuddy:
    def __init__(self, model=None):
        self.turns = 0

    def _run_tool(self, name, arguments):
        return {"status": "error", "code": "MISSING_BUDGET"}

    def chat(self, message):
        self.turns += 1
        if self.turns == 1:
            self._run_tool("calculate_budget_per_night", {})
        return f"lượt {self.turns}: {message}"


class Server(unittest.TestCase):
    def setUp(self):
        for name, value in (("TravelBuddy", FakeBuddy), ("get_model", mock.Mock())):
            patcher = mock.patch.object(demo, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(demo._sessions.clear)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), demo.Handler)
        self.server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, response.read()

    def chat(self, payload, headers=None):
        status, body = self.request(
            "POST", "/chat", json.dumps(payload), headers or {"Content-Type": "application/json"}
        )
        return status, json.loads(body)

    def test_page_is_served(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("TravelBuddy demo".encode(), body)

    def test_session_keeps_history_and_reports_tools_once(self):
        status, first = self.chat({"session": "a", "message": " xin chào "})
        self.assertEqual((status, first["reply"]), (200, "lượt 1: xin chào"))
        self.assertEqual(first["tools"][0]["code"], "MISSING_BUDGET")
        _, second = self.chat({"session": "a", "message": "tiếp"})
        self.assertEqual((second["reply"], second["tools"]), ("lượt 2: tiếp", []))
        _, other = self.chat({"session": "b", "message": "mới"})
        self.assertEqual(other["reply"], "lượt 1: mới")

    def test_rejects_bad_requests(self):
        for payload in [{"message": "x"}, {"session": "a"}, {"session": "a", "message": "  "}, ["x"]]:
            self.assertEqual(self.chat(payload)[0], 400, payload)
        self.assertEqual(self.chat({"session": "a", "message": "x" * 2001})[0], 400)
        self.assertEqual(self.chat({"session": "a", "message": "x"}, {"Content-Type": "text/plain"})[0], 415)
        foreign = {"Content-Type": "application/json", "Host": "evil.example"}
        self.assertEqual(self.chat({"session": "a", "message": "x"}, foreign)[0], 403)
        self.assertEqual(self.request("POST", "/chat", "{oops", {"Content-Type": "application/json"})[0], 400)

    def test_startup_failure_is_reported_as_json(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with mock.patch.object(demo, "TravelBuddy", side_effect=ValueError("no key")):
            status, body = self.chat({"session": "z", "message": "x"})
        self.assertEqual(status, 500)
        self.assertIn("API key", body["error"])

    def test_provider_is_selected_and_validated(self):
        self.chat({"session": "a", "message": "x", "provider": "nvidia"})
        demo.get_model.assert_called_once_with("nvidia")
        self.assertEqual(self.chat({"session": "a", "message": "x", "provider": "evil"})[0], 400)
        self.assertEqual(self.chat({"session": "a", "message": "x"})[0], 200)
        demo.get_model.assert_called_with("gemini")


if __name__ == "__main__":
    unittest.main()
