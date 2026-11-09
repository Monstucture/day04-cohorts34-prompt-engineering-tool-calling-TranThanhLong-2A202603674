"""
run_tests.py — Task 4: 5 câu test chứng minh KHI NÀO agent trả lời trực tiếp,
KHI NÀO gọi tool.

Bộ test bám sát "System Prompt Testing Checklist":
  - Happy path (gọi đúng tool)
  - Tool decision (khi nào tool vs khi nào direct)
  - Edge case (thiếu thông tin bắt buộc -> hỏi lại, không tool)
  - Out of scope (từ chối đúng cách, không tool)

Mỗi câu có `expect_tool` (kỳ vọng) để tự động kiểm tra hành vi tool-decision.
"""

from __future__ import annotations

from agent import run_agent
from llm import get_model

# (câu hỏi, tool kỳ vọng hoặc None nếu kỳ vọng trả lời trực tiếp, ghi chú)
TEST_CASES = [
    ("Tìm chuyến bay HAN đến DAD ngày 2099-01-01 cho 2 người",
     "search_flights",
     "Happy path: đủ điểm đi, điểm đến, ngày bay, số khách -> GỌI TOOL."),

    ("Thời tiết Hà Nội hôm nay thế nào?",
     None,
     "Ngoài phạm vi: không có tool thời tiết -> TRẢ LỜI TRỰC TIẾP, không tool."),

    ("Xin chào, bạn là ai?",
     None,
     "Conditional/direct: chào hỏi -> TRẢ LỜI TRỰC TIẾP, không tool."),

    ("Cho tôi tìm chuyến bay đi.",
     None,
     "Edge case: tìm chuyến bay nhưng THIẾU thông tin -> hỏi lại, KHÔNG tool."),

    ("Bạn nghĩ giá bitcoin ngày mai thế nào?",
     None,
     "Out of scope: lời khuyên tài chính -> TỪ CHỐI trực tiếp, không tool."),
]


def main() -> None:
    model = get_model()  # mặc định MockModel; đặt LAB_MODEL=gemini để dùng model thật
    passed = 0
    summary = []

    for i, (question, expected_tool, note) in enumerate(TEST_CASES, 1):
        print(f"\n########## TEST {i}: {note}")
        trace = run_agent(question, model=model)

        actual_tool = trace.tool_calls[0] if trace.called_tool else None
        ok = actual_tool == expected_tool
        passed += ok

        exp = expected_tool or "DIRECT (không tool)"
        act = actual_tool or "DIRECT (không tool)"
        print(f"{'-'*70}")
        print(f"  Kỳ vọng: {exp:32}  Thực tế: {act:32}  {'✅ PASS' if ok else '❌ FAIL'}")
        summary.append((i, exp, act, ok))

    print(f"\n{'='*70}")
    print(f"KẾT QUẢ: {passed}/{len(TEST_CASES)} test PASS")
    print(f"{'='*70}")
    print(f"{'#':<3}{'Kỳ vọng':<34}{'Thực tế':<34}{'':<6}")
    for i, exp, act, ok in summary:
        print(f"{i:<3}{exp:<34}{act:<34}{'PASS' if ok else 'FAIL'}")
    print("\nGhi chú: 1 câu GỌI TOOL, 4 câu TRẢ LỜI TRỰC TIẾP (không có tool phù hợp /")
    print("chào hỏi / thiếu thông tin / ngoài phạm vi). Đúng như thiết kế policy.")


if __name__ == "__main__":
    main()
