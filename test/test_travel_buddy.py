"""Test harness không cần mạng: python -m unittest discover -s test -p test_travel_buddy.py"""

import json
import logging
import os
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from google.genai import errors, types

import llm
from tools import TOOLS
from travel_buddy import ERROR_REPLY, EMPTY_REPLY, MAX_STEPS, TravelBuddy, _mentioned_amounts

DAY = date.today() + timedelta(days=3)
FLIGHT = {"origin": "HAN", "destination": "DAD", "departure_date": DAY.isoformat(), "travelers": 2}
BUDGET = {"total_budget": 10000000, "nights": 3, "cheapest_flights_total": 1, "destination": "Đà Nẵng", "travelers": 9}
HOTEL = {"city": "Đà Nẵng", "max_price_per_night": 99999999}


def reply(text=None, calls=()):
    content = types.Content(role="model", parts=[types.Part.from_text(text=text or "call")])
    return SimpleNamespace(
        text=text,
        function_calls=[SimpleNamespace(name=n, args=a) for n, a in calls],
        candidates=[SimpleNamespace(content=content)],
    )


def fake_model(responses):
    generate = mock.Mock(side_effect=responses)
    return SimpleNamespace(
        _types=types,
        model="fake",
        client=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
        _tool_config=lambda schemas: types.Tool(),
        _call_with_retry=lambda fn, **kwargs: fn(**kwargs),
    )


class Base(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if k != "SERPAPI_API_KEY"}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.buddy = TravelBuddy(model=fake_model([]))


class Guards(Base):
    def test_missing_budget_stops_at_step_two(self):
        self.buddy.user_texts.append("Đi từ HAN tới DAD, 2 người, 3 đêm")
        self.assertEqual(self.buddy._run_tool("search_flights", FLIGHT)["status"], "success")
        self.assertEqual(self.buddy._run_tool("calculate_budget_per_night", BUDGET)["code"], "MISSING_BUDGET")
        self.assertEqual(self.buddy._run_tool("search_hotels", HOTEL)["code"], "BUDGET_REQUIRED")

    def test_hotels_cannot_skip_the_sequence(self):
        self.buddy.user_texts.append("ngân sách 10 triệu")
        self.assertEqual(self.buddy._run_tool("calculate_budget_per_night", BUDGET)["code"], "FLIGHTS_REQUIRED")
        self.assertEqual(self.buddy._run_tool("search_hotels", HOTEL)["code"], "BUDGET_REQUIRED")

    def test_full_sequence_uses_values_from_earlier_steps(self):
        self.buddy.user_texts.append("ngân sách 10 triệu")
        self.buddy._run_tool("search_flights", FLIGHT)
        data = self.buddy._run_tool("calculate_budget_per_night", BUDGET)["data"]
        self.assertEqual(data["cheapest_flights_total"], 2560000)
        self.assertEqual(data["travelers"], 2)
        result = self.buddy._run_tool("search_hotels", HOTEL)["data"]
        self.assertEqual(result["max_price_per_night"], data["remaining_per_night"])
        self.assertEqual(result["check_in_date"], DAY.isoformat())
        self.assertEqual(result["check_out_date"], (DAY + timedelta(days=3)).isoformat())

    def test_model_may_lower_hotel_limit_for_multiple_rooms(self):
        self.buddy.user_texts.append("ngân sách 10 triệu")
        self.buddy._run_tool("search_flights", FLIGHT)
        self.buddy._run_tool("calculate_budget_per_night", BUDGET)
        result = self.buddy._run_tool("search_hotels", {"city": "Đà Nẵng", "max_price_per_night": 600000})
        self.assertEqual(result["data"]["max_price_per_night"], 600000)

    def test_over_budget_blocks_hotels(self):
        self.buddy.user_texts.append("ngân sách 1 triệu")
        self.buddy._run_tool("search_flights", FLIGHT)
        result = self.buddy._run_tool("calculate_budget_per_night", {**BUDGET, "total_budget": 1000000})
        self.assertFalse(result["data"]["within_budget"])
        self.assertEqual(self.buddy._run_tool("search_hotels", HOTEL)["code"], "OVER_BUDGET")

    def test_new_flight_search_invalidates_budget(self):
        self.buddy.user_texts.append("ngân sách 10 triệu")
        self.buddy._run_tool("search_flights", FLIGHT)
        self.buddy._run_tool("calculate_budget_per_night", BUDGET)
        self.buddy._run_tool("search_flights", FLIGHT)
        self.assertEqual(self.buddy._run_tool("search_hotels", HOTEL)["code"], "BUDGET_REQUIRED")

    def test_per_person_budget_counts_as_stated(self):
        self.buddy.user_texts.append("mỗi người 5 triệu")
        self.buddy._run_tool("search_flights", FLIGHT)
        self.assertEqual(self.buddy._run_tool("calculate_budget_per_night", BUDGET)["status"], "success")

    def test_unknown_tool(self):
        self.assertEqual(self.buddy._run_tool("get_weather", {"city": "Huế"})["code"], "TOOL_NOT_FOUND")


class ToolDocs(unittest.TestCase):
    def test_only_travel_tools_are_registered(self):
        self.assertEqual(list(TOOLS), ["search_flights", "calculate_budget_per_night", "search_hotels"])

    def test_every_docstring_is_the_schema_description(self):
        for tool in TOOLS.values():
            description = tool["schema"]["function"]["description"]
            for section in ("Dùng KHI", "KHÔNG", "Args:", "Returns:"):
                self.assertIn(section, description)
            for parameter in tool["schema"]["function"]["parameters"]["properties"]:
                self.assertIn(f"{parameter}:", description)


class Amounts(unittest.TestCase):
    def test_vietnamese_amounts(self):
        for text, expected in [
            ("10 triệu", 10000000), ("10tr", 10000000), ("1,5 triệu", 1500000), ("10.000.000 đồng", 10000000),
            ("2 tỷ", 2000000000), ("500k", 500000), ("800 nghìn", 800000), ("12 củ", 12000000),
            ("10 TRIỆU", 10000000), ("10 triệu", 10000000),
        ]:
            self.assertIn(expected, _mentioned_amounts(text), text)

    def test_tr_prefix_of_another_word_is_not_a_unit(self):
        self.assertEqual(_mentioned_amounts("2 trẻ em"), {2})


class NvidiaAdapter(Base):
    def run_chat(self, *messages):
        sent = []

        def opener(request, timeout):
            sent.append(request)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(
                {"choices": [{"message": messages[len(sent) - 1]}]}
            ).encode()
            return response

        os.environ["NVIDIA_API_KEY"] = "test-key"
        buddy = TravelBuddy(model=llm._NvidiaModel())
        with mock.patch("urllib.request.urlopen", opener):
            return buddy.chat("Đi HAN tới DAD 2 người"), sent

    def test_tool_loop_uses_openai_format(self):
        call = {"id": "server-id", "type": "function",
                "function": {"name": "search_flights", "arguments": json.dumps(FLIGHT)}}
        answer, sent = self.run_chat({"tool_calls": [call]}, {"content": "Bạn có ngân sách bao nhiêu?"})
        self.assertEqual(answer, "Bạn có ngân sách bao nhiêu?")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer test-key")
        first = json.loads(sent[0].data)
        self.assertEqual([t["function"]["name"] for t in first["tools"]],
                         ["search_flights", "calculate_budget_per_night", "search_hotels"])
        self.assertEqual(first["tools"][0]["function"]["parameters"]["required"][0], "origin")
        roles = json.loads(sent[1].data)["messages"]
        self.assertEqual([m["role"] for m in roles], ["system", "user", "assistant", "tool"])
        self.assertEqual(roles[3]["tool_call_id"], roles[2]["tool_calls"][0]["id"])
        self.assertEqual(json.loads(roles[3]["content"])["status"], "success")

    def test_agent_flow_decide_then_summarize(self):
        call = {"id": "x", "type": "function",
                "function": {"name": "search_flights", "arguments": json.dumps(FLIGHT)}}
        answers = iter([{"tool_calls": [call]}, {"content": '{"reply": "ok"}'}])
        sent = []

        def opener(request, timeout):
            sent.append(json.loads(request.data))
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(
                {"choices": [{"message": next(answers)}]}
            ).encode()
            return response

        os.environ["NVIDIA_API_KEY"] = "test-key"
        model = llm._NvidiaModel()
        schemas = [TOOLS["search_flights"]["schema"]]
        with mock.patch("urllib.request.urlopen", opener):
            decided = model.decide("hệ thống", "tìm vé", ["search_flights"], schemas)
            summary = model.summarize_tool_result("search_flights", {"status": "success"}, "tìm vé")
        self.assertEqual((decided.tool_calls[0].name, decided.tool_calls[0].arguments), ("search_flights", FLIGHT))
        self.assertEqual(summary.text, '{"reply": "ok"}')
        self.assertEqual([m["role"] for m in sent[0]["messages"]], ["system", "user"])
        self.assertEqual([m["role"] for m in sent[1]["messages"]], ["user"])
        self.assertNotIn("tools", sent[1])

    def test_missing_key_is_reported(self):
        os.environ.pop("NVIDIA_API_KEY", None)
        with self.assertRaises(ValueError):
            llm._NvidiaModel()


class GeminiRetry(unittest.TestCase):
    def setUp(self):
        self.model = llm._GeminiModel.__new__(llm._GeminiModel)
        self.model._errors = errors
        for target in ("llm.time.sleep", "builtins.print"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_retries_when_gemini_is_overloaded(self):
        for error in (errors.ServerError(503, {}), errors.ClientError(429, {})):
            call = mock.Mock(side_effect=[error, "ok"])
            self.assertEqual(self.model._call_with_retry(call), "ok")
            self.assertEqual(call.call_count, 2)

    def test_exhausted_daily_quota_fails_instead_of_sleeping_for_hours(self):
        call = mock.Mock(side_effect=errors.ClientError(429, {"retryDelay": "72163s"}))
        with self.assertRaises(errors.ClientError):
            self.model._call_with_retry(call)
        self.assertEqual(call.call_count, 1)

    def test_other_errors_are_not_retried(self):
        call = mock.Mock(side_effect=errors.ClientError(404, {}))
        with self.assertRaises(errors.ClientError):
            self.model._call_with_retry(call)
        self.assertEqual(call.call_count, 1)


class Loop(Base):
    def test_budget_refusal_is_fed_back_and_turn_finishes(self):
        calls = [("search_flights", FLIGHT), ("calculate_budget_per_night", BUDGET), ("search_hotels", HOTEL)]
        buddy = TravelBuddy(model=fake_model([reply(calls=calls), reply("Bạn có ngân sách bao nhiêu?")]))
        self.assertEqual(buddy.chat("Đi HAN tới DAD 2 người"), "Bạn có ngân sách bao nhiêu?")
        codes = [p.function_response.response.get("code") for p in buddy.contents[2].parts]
        self.assertEqual(codes, [None, "MISSING_BUDGET", "BUDGET_REQUIRED"])

    def test_api_failure_rolls_back_turn(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        buddy = TravelBuddy(model=fake_model([RuntimeError("boom")]))
        self.assertEqual(buddy.chat("xin chào"), ERROR_REPLY)
        self.assertEqual((buddy.contents, buddy.user_texts), ([], []))

    def test_failed_turn_does_not_leave_budget_behind(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        calls = [("search_flights", FLIGHT), ("calculate_budget_per_night", BUDGET)]
        buddy = TravelBuddy(model=fake_model([reply(calls=calls), RuntimeError("503")]))
        self.assertEqual(buddy.chat("ngân sách 10 triệu"), ERROR_REPLY)
        self.assertEqual(buddy._run_tool("search_hotels", HOTEL)["code"], "BUDGET_REQUIRED")

    def test_step_limit_stops_tool_loop(self):
        looping = [reply(calls=[("search_flights", FLIGHT)]) for _ in range(MAX_STEPS)]
        buddy = TravelBuddy(model=fake_model(looping))
        self.assertEqual(buddy.chat("đi"), ERROR_REPLY)
        self.assertEqual(buddy.contents, [])

    def test_empty_model_reply(self):
        buddy = TravelBuddy(model=fake_model([reply()]))
        self.assertEqual(buddy.chat("?"), EMPTY_REPLY)


if __name__ == "__main__":
    unittest.main()
