"""
travel_buddy.py — Harness hội thoại: search_flights -> calculate_budget_per_night -> search_hotels.

Prompt mô tả hành vi, còn các luật không được phép sai thì code chặn trong _run_tool:
thứ tự tool, budget phải do khách nói, và tiền vé/số khách/ngày lưu trú lấy từ kết quả
bước trước thay vì tin model chép lại.

Chạy: python travel_buddy.py (cần GEMINI_API_KEY trong .env)
Đổi sang NVIDIA: đặt TRAVEL_BUDDY_MODEL=nvidia và NVIDIA_API_KEY.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import date, datetime, timedelta

from llm import get_model
from system_prompt import TRAVEL_BUDDY_PROMPT
from tools import TOOLS, _is_amount, execute_tool

TRAVEL_TOOLS = ("search_flights", "calculate_budget_per_night", "search_hotels")
MAX_STEPS = 6
ERROR_REPLY = "Xin lỗi, mình đang gặp sự cố kỹ thuật. Bạn vui lòng thử lại sau ít phút nhé."
EMPTY_REPLY = "Xin lỗi, mình chưa thể trả lời yêu cầu này. Bạn có thể nói rõ hơn về chuyến đi của mình không?"

_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)*)\s*(tỷ|tỉ|triệu|củ|tr|nghìn|ngàn|k)?(?!\w)", re.IGNORECASE)
_UNITS = {"tỷ": 10**9, "tỉ": 10**9, "triệu": 10**6, "củ": 10**6, "tr": 10**6, "nghìn": 1000, "ngàn": 1000, "k": 1000}


def _mentioned_amounts(text: str) -> set[int]:
    """Các số tiền xuất hiện trong lời khách, ví dụ '1,5 triệu', '10tr', '1.500.000'."""
    amounts = set()
    for raw, unit in _AMOUNT.findall(unicodedata.normalize("NFC", text)):
        if unit and re.fullmatch(r"\d+[.,]\d{1,2}", raw):
            number = float(raw.replace(",", "."))
        else:
            number = float(re.sub(r"[.,]", "", raw))
        amounts.add(round(number * _UNITS.get(unit.lower(), 1)))
    return amounts


def _blocked(code: str, message: str) -> dict:
    return {"status": "error", "message": message, "code": code}


class TravelBuddy:
    def __init__(self, model=None):
        self.model = model or get_model(os.environ.get("TRAVEL_BUDDY_MODEL", "gemini"))
        self.contents: list = []
        self.user_texts: list[str] = []
        self._flights: dict | None = None  # kết quả bước 1 còn hiệu lực
        self._budget: dict | None = None  # kết quả bước 2 còn hiệu lực

    def _budget_was_stated(self, value, travelers: int) -> bool:
        if not _is_amount(value):
            return False
        stated = set().union(*(_mentioned_amounts(text) for text in self.user_texts))
        # Khách có thể nói "mỗi người 5 triệu"; model nhân lên thành tổng của nhóm.
        return round(value) in stated or round(value / travelers) in stated

    def _run_tool(self, name: str, arguments: dict) -> dict:
        args = dict(arguments)
        if name == "search_flights":
            return self._run_search_flights(args)
        if name == "calculate_budget_per_night":
            return self._run_calculate_budget(args)
        if name == "search_hotels":
            return self._run_search_hotels(args)
        return _blocked("TOOL_NOT_FOUND", f"TravelBuddy không có tool '{name}'.")

    def _run_search_flights(self, args: dict) -> dict:
        # Tìm chuyến mới thì ngân sách và ngày lưu trú cũ không còn đúng.
        self._flights = self._budget = None
        result = execute_tool("search_flights", args)
        if result["status"] == "success":
            data = result["data"]
            prices = [f["total_price"] for f in data["flights"] if f["total_price"] is not None]
            if prices:
                self._flights = {
                    "cheapest": min(prices),
                    "travelers": data["travelers"],
                    "departure_date": data["departure_date"],
                }
        return result

    def _run_calculate_budget(self, args: dict) -> dict:
        if not self._flights:
            return _blocked("FLIGHTS_REQUIRED", "Cần có kết quả chuyến bay có giá trước khi tính ngân sách.")
        if not self._budget_was_stated(args.get("total_budget"), self._flights["travelers"]):
            return _blocked(
                "MISSING_BUDGET",
                "Chưa đọc được ngân sách từ lời khách. Dừng tại đây và không gợi ý khách sạn. Nếu khách chưa "
                "nêu, hỏi tổng ngân sách; nếu khách đã nêu bằng chữ, xin ghi lại bằng số, ví dụ 10500000.",
            )
        args.update(cheapest_flights_total=self._flights["cheapest"], travelers=self._flights["travelers"])
        self._budget = None
        result = execute_tool("calculate_budget_per_night", args)
        if result["status"] == "success":
            self._budget = result["data"]
        return result

    def _run_search_hotels(self, args: dict) -> dict:
        if not self._budget:
            return _blocked(
                "BUDGET_REQUIRED",
                "Chưa tính được ngân sách. Hỏi khách ngân sách và số đêm; không gợi ý khách sạn.",
            )
        limit = self._budget["remaining_per_night"]
        if limit <= 0:
            return _blocked("OVER_BUDGET", "Tiền vé đã dùng hết ngân sách; không tìm khách sạn.")
        requested = args.get("max_price_per_night")
        check_in = datetime.strptime(self._flights["departure_date"], "%Y-%m-%d").date()
        args.update(
            max_price_per_night=requested if _is_amount(requested) and 0 < requested <= limit else limit,
            check_in_date=check_in.isoformat(),
            check_out_date=(check_in + timedelta(days=self._budget["nights"])).isoformat(),
        )
        return execute_tool("search_hotels", args)

    def chat(self, user_msg: str) -> str:
        types = self.model._types
        start = len(self.contents), len(self.user_texts)
        saved_state = self._flights, self._budget
        self.user_texts.append(user_msg)
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_msg)]))
        config = types.GenerateContentConfig(
            system_instruction=TRAVEL_BUDDY_PROMPT.format(today=date.today().isoformat()),
            tools=[self.model._tool_config([TOOLS[name]["schema"] for name in TRAVEL_TOOLS])],
        )
        try:
            for _ in range(MAX_STEPS):
                resp = self.model._call_with_retry(
                    self.model.client.models.generate_content,
                    model=self.model.model, contents=self.contents, config=config,
                )
                if not resp.function_calls:
                    if not resp.text:  # bị bộ lọc an toàn chặn hoặc phản hồi rỗng
                        return EMPTY_REPLY
                    self.contents.append(resp.candidates[0].content)
                    return resp.text
                # Giữ nguyên content của model: Gemini 3 cần thought signature ở lượt sau.
                self.contents.append(resp.candidates[0].content)
                self.contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=call.name, response=self._run_tool(call.name, dict(call.args)))
                    for call in resp.function_calls
                ]))
        except Exception:  # ranh giới với khách: không để traceback lộ ra hội thoại
            logging.exception("TravelBuddy: gọi Gemini thất bại")
        # Bỏ lượt hỏng để lịch sử không còn tool call dở dang.
        del self.contents[start[0]:], self.user_texts[start[1]:]
        self._flights, self._budget = saved_state
        return ERROR_REPLY


def main() -> None:
    buddy = TravelBuddy()
    print("TravelBuddy: Chào bạn! Bạn muốn đi đâu, khi nào, mấy người? (gõ 'thoát' để kết thúc)")
    while True:
        try:
            user_msg = input("Bạn: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_msg.lower() in {"thoát", "exit", "quit"}:
            break
        if user_msg:
            print("TravelBuddy:", buddy.chat(user_msg))


if __name__ == "__main__":
    main()
