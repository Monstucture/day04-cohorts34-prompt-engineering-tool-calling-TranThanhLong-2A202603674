"""
demo_errors.py — Task 5: Tái hiện & phân loại lỗi thành 3 nhóm.

Câu hỏi mở đầu buổi học: "cùng một agent, đôi khi gọi tool đúng, đôi khi sai —
DO PROMPT hay DO TOOL?". File này trả lời bằng cách CỐ Ý gây ra 3 loại lỗi rồi
phân loại + sửa:

  1. LỖI PROMPT        — system prompt mơ hồ/thiếu ràng buộc -> hành vi sai.
  2. LỖI TOOL SCHEMA   — schema mô tả kém/thiếu enum -> model truyền sai arguments.
  3. LỖI CONTROL FLOW  — vòng lặp agent sai (quên feed tool result) -> câu trả lời hỏng.

Chạy: python demo_errors.py   (mỗi demo in SYMPTOM -> DIAGNOSIS -> FIX)
Chi tiết phân tích nằm trong errors.md.
"""

from __future__ import annotations

import json

from agent import run_agent
from llm import MockModel
from system_prompt import BROKEN_SYSTEM_PROMPT, SYSTEM_PROMPT
from tools import execute_tool


def banner(title: str) -> None:
    print(f"\n{'#'*72}\n# {title}\n{'#'*72}")


# ---------------------------------------------------------------------------
# LỖI 1 — PROMPT: prompt mơ hồ, thiếu Constraints -> không từ chối, bịa best-guess.
# ---------------------------------------------------------------------------
def demo_prompt_error() -> None:
    banner("LỖI 1 — PROMPT: thiếu ràng buộc 'ngoài phạm vi' -> agent bịa")
    q = "Bạn nghĩ giá bitcoin ngày mai thế nào?"
    model = MockModel()  # model suy policy TỪ system prompt

    print(">> SYMPTOM (dùng BROKEN_SYSTEM_PROMPT — 'make your best guess'):")
    t_bad = run_agent(q, model=model, system_prompt=BROKEN_SYSTEM_PROMPT, verbose=False)
    print("   FINAL:", t_bad.final_reply)

    print("\n>> DIAGNOSIS: prompt không có ràng buộc NEVER về tài chính/ngoài phạm vi,")
    print("   lại ép 'make your best guess' -> model trả lời câu ngoài phạm vi. Đây là")
    print("   LỖI PROMPT (không phải tool): tool/loop không đổi, chỉ prompt sai.")

    print("\n>> FIX (dùng SYSTEM_PROMPT có Constraints 'NEVER lời khuyên tài chính'):")
    t_ok = run_agent(q, model=model, system_prompt=SYSTEM_PROMPT, verbose=False)
    print("   FINAL:", t_ok.final_reply)


# ---------------------------------------------------------------------------
# LỖI 2 — TOOL SCHEMA: thiếu mô tả định dạng trong schema -> model truyền sai giá trị.
# ---------------------------------------------------------------------------
def demo_tool_schema_error() -> None:
    banner("LỖI 2 — TOOL SCHEMA: thiếu mô tả định dạng -> arguments sai giá trị")

    print(">> SYMPTOM: schema search_flights KHÔNG nói 'origin' phải là mã IATA.")
    print("   Model đoán theo ngôn ngữ người dùng -> truyền 'Hà Nội':")
    trip = {"destination": "DAD", "departure_date": "2099-01-01", "travelers": 2}
    bad_args = {"origin": "Hà Nội", **trip}
    res_bad = execute_tool("search_flights", bad_args)
    print("   execute_tool('search_flights',", bad_args, ") ->")
    print("   ", json.dumps(res_bad, ensure_ascii=False))

    print("\n>> DIAGNOSIS: hàm chạy đúng, prompt đúng, nhưng ARGUMENTS sai vì schema")
    print("   không ràng buộc định dạng hợp lệ. Đây là LỖI TOOL SCHEMA.")

    print("\n>> FIX: mô tả 'Mã IATA sân bay đi, ví dụ HAN' cho 'origin'")
    print("   (đã có sẵn trong tools.py) để model truyền đúng mã:")
    good_args = {"origin": "HAN", **trip}
    res_ok = execute_tool("search_flights", good_args)
    print("   execute_tool('search_flights',", good_args, ") ->")
    print("   ", json.dumps(res_ok, ensure_ascii=False))


# ---------------------------------------------------------------------------
# LỖI 3 — CONTROL FLOW: vòng lặp quên feed tool result trở lại model.
# ---------------------------------------------------------------------------
def run_agent_broken(user_msg: str, model=None) -> str:
    """Bản agent loop HỎNG: gọi tool nhưng KHÔNG feed kết quả lại cho model."""
    model = model or MockModel()
    resp = model.decide(SYSTEM_PROMPT, user_msg, ["search_flights"])
    if resp.wants_tool:
        call = resp.tool_calls[0]
        _ = execute_tool(call.name, call.arguments)  # BUG: bỏ quên kết quả!
        # Quên bước "summarize_tool_result" -> không có câu trả lời cuối.
        return "(không có câu trả lời — model chưa từng thấy kết quả tool)"
    return resp.text


def demo_control_flow_error() -> None:
    banner("LỖI 3 — CONTROL FLOW: quên feed tool result -> câu trả lời rỗng")
    q = "Tìm chuyến bay HAN đến DAD ngày 2099-01-01 cho 2 người"

    print(">> SYMPTOM (run_agent_broken — thiếu bước feed kết quả về model):")
    print("   FINAL:", run_agent_broken(q))

    print("\n>> DIAGNOSIS: tool được gọi đúng, schema đúng, prompt đúng — nhưng VÒNG LẶP")
    print("   agent thiếu bước 4 (tool result -> LLM final). Đây là LỖI CONTROL FLOW.")

    print("\n>> FIX (run_agent chuẩn — có đủ 4 bước của Tool Calling Flow):")
    t_ok = run_agent(q, verbose=False)
    print("   FINAL:", t_ok.final_reply)


if __name__ == "__main__":
    demo_prompt_error()
    demo_tool_schema_error()
    demo_control_flow_error()
    print("\nTóm lại: cùng một câu hỏi có thể hỏng vì PROMPT, vì TOOL SCHEMA, hoặc vì")
    print("CONTROL FLOW. Chẩn đoán đúng NHÓM lỗi mới sửa đúng chỗ. Xem errors.md.")
