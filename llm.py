"""
llm.py — Lớp model có thể thay thế (pluggable), chuẩn hoá về một interface chung.

Slide "Tool Calling Flow": LLM decides -> tool_call JSON -> App executes -> tool
result -> LLM final response. File này lo phần "LLM decides" và "LLM final".

Mặc định dùng MockModel: một model GIẢ LẬP có luật quyết định (rule-based router)
để lab CHẠY ĐƯỢC ngay, offline, kết quả tất định (dễ demo, dễ chấm). Ai có API key
có thể chuyển sang model thật (Anthropic/OpenAI) — vòng lặp agent không đổi.

Chuẩn hoá: mọi model trả về ModelResponse gồm:
  - text: câu trả lời trực tiếp (khi KHÔNG gọi tool), hoặc None
  - tool_calls: danh sách ToolCall(name, arguments, id), rỗng nếu trả lời trực tiếp
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from types import SimpleNamespace

from dotenv import load_dotenv
load_dotenv()


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = "call_1"


@dataclass
class ModelResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tool(self) -> bool:
        return len(self.tool_calls) > 0


# ---------------------------------------------------------------------------
# MOCK MODEL — router tất định, dùng để dạy vòng lặp tool calling offline.
# ---------------------------------------------------------------------------
class MockModel:
    """Mô phỏng 'khi nào model gọi tool, khi nào trả lời trực tiếp'.

    Đây KHÔNG phải LLM thật; nó là bộ luật đơn giản để minh hoạ 3 pattern
    (slide "3 Tool Use Patterns"): conditional / direct / refusal. Quyết định
    dựa trên từ khoá trong câu hỏi + policy trong system prompt.
    """

    def __init__(self, honor_out_of_scope: bool | None = None):
        # None (mặc định): model SUY RA policy từ system prompt (prompt tốt -> có
        #   ràng buộc tài chính -> từ chối; prompt kém -> không có -> bịa best-guess).
        #   Đây là cách demo LỖI PROMPT: cùng model, đổi prompt -> đổi hành vi.
        # True/False: ép cứng, dùng khi cần cô lập biến trong test.
        self.honor_out_of_scope = honor_out_of_scope

    def _honors_scope(self, system_prompt: str) -> bool:
        if self.honor_out_of_scope is not None:
            return self.honor_out_of_scope
        sp = system_prompt.lower()
        return "tài chính" in sp or "đầu tư" in sp or "ngoài phạm vi" in sp

    _FLIGHT_KW = ("chuyến bay", "vé máy bay", "flight")
    _FINANCE_KW = ("bitcoin", "cổ phiếu", "co phieu", "crypto", "đầu tư", "chứng khoán", "giá vàng")

    def decide(self, system_prompt: str, user_msg: str, tool_names: list[str],
               tools_full: list[dict] | None = None) -> ModelResponse:
        # tools_full: MockModel không cần schema đầy đủ (nó route bằng từ khoá);
        # tham số này chỉ để signature khớp với model thật (Gemini).
        text = user_msg.lower()

        # (1) Ngoài phạm vi: tài chính/đầu tư.
        if any(k in text for k in self._FINANCE_KW):
            if self._honors_scope(system_prompt):
                # Prompt tốt (có Constraints) -> từ chối trực tiếp.
                return ModelResponse(text=json.dumps({
                    "intent": "hỏi lời khuyên tài chính (ngoài phạm vi)",
                    "action": "direct",
                    "reply": "Xin lỗi, mình chỉ hỗ trợ tra cứu chuyến bay, ngân sách và "
                             "khách sạn, không đưa lời khuyên đầu tư/tài chính.",
                }, ensure_ascii=False))
            # Prompt kém (ép 'make your best guess') -> BỊA câu trả lời nguy hiểm.
            return ModelResponse(text=json.dumps({
                "intent": "hỏi lời khuyên tài chính",
                "action": "direct",
                "reply": "Theo dự đoán của mình, giá bitcoin ngày mai có thể tăng ~5%. "
                         "(⚠ bịa — model không có dữ liệu này)",
            }, ensure_ascii=False))

        # (2) Ý định tìm chuyến bay.
        if any(k in text for k in self._FLIGHT_KW) and "search_flights" in tool_names:
            route = re.findall(r"\b[A-Z]{3}\b", user_msg)
            day = re.search(r"\d{4}-\d{2}-\d{2}", user_msg)
            people = re.search(r"(\d+)\s*(?:người|khách)", text)
            if len(route) < 2 or not day or not people:
                # Thiếu required field -> KHÔNG gọi tool, hỏi lại (Rules).
                return ModelResponse(text=json.dumps({
                    "intent": "tìm chuyến bay nhưng thiếu thông tin",
                    "action": "direct",
                    "reply": "Bạn cho mình biết điểm đi, điểm đến, ngày bay và số hành khách nhé.",
                }, ensure_ascii=False))
            return ModelResponse(tool_calls=[ToolCall("search_flights", {
                "origin": route[0], "destination": route[1],
                "departure_date": day.group(), "travelers": int(people.group(1)),
            })])

        # (3) Chào hỏi / hỏi bạn là ai -> trả lời trực tiếp, không tool.
        if any(k in text for k in ("xin chào", "chào", "bạn là ai", "hello", "hi ")):
            return ModelResponse(text=json.dumps({
                "intent": "chào hỏi / hỏi danh tính",
                "action": "direct",
                "reply": "Chào bạn! Mình là TravelBuddy, trợ lý tra cứu chuyến bay, ngân sách và khách sạn.",
            }, ensure_ascii=False))

        # (4) Không khớp intent nào -> trả lời trực tiếp (không bịa, không gọi tool).
        return ModelResponse(text=json.dumps({
            "intent": "không xác định",
            "action": "direct",
            "reply": "Mình chưa rõ yêu cầu. Bạn có thể hỏi về chuyến bay, ngân sách "
                     "hoặc khách sạn.",
        }, ensure_ascii=False))

    def summarize_tool_result(self, tool_name: str, tool_result: dict, user_msg: str) -> ModelResponse:
        """Lượt 2 của model: biến tool result thành câu trả lời cuối (JSON contract)."""
        if tool_result.get("status") == "error":
            reply = f"Không lấy được dữ liệu: {tool_result.get('message')}."
        elif tool_name == "search_flights":
            d = tool_result["data"]
            cheapest = min(f["total_price"] for f in d["flights"])
            reply = (f"Có {len(d['flights'])} chuyến {d['origin']} -> {d['destination']}, "
                     f"rẻ nhất {cheapest:,} VND.")
        else:
            reply = "Đã có kết quả."
        return ModelResponse(text=json.dumps({
            "intent": user_msg, "action": tool_name, "reply": reply,
        }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# REAL MODELS (tuỳ chọn) — chỉ để tham khảo; cần cài SDK + API key.
# Vòng lặp agent.py hoạt động y hệt với các model này.
# ---------------------------------------------------------------------------
def get_model(name: str | None = None):
    """Chọn model theo biến môi trường LAB_MODEL: mock | gemini | anthropic | nvidia."""
    name = name or os.environ.get("LAB_MODEL", "mock")
    if name == "mock":
        return MockModel()
    if name == "gemini":
        return _GeminiModel()
    if name == "anthropic":
        return _AnthropicModel()
    if name == "nvidia":
        return _NvidiaModel()
    raise ValueError(f"Model không hỗ trợ: {name}")


class _GeminiModel:
    """Adapter cho Google Gemini qua Gen AI SDK (cần `pip install google-genai`).

    Xác thực (tự đọc từ biến môi trường khi tạo genai.Client()):
      - Gemini Developer API (đơn giản, cho lab):  export GEMINI_API_KEY=...
      - Agent Platform (Vertex AI, cho doanh nghiệp):
            export GOOGLE_GENAI_USE_VERTEXAI=true
            export GOOGLE_CLOUD_PROJECT=... ; export GOOGLE_CLOUD_LOCATION=global

    Ghi chú tool calling của Gemini (khác OpenAI):
      - Schema tool khai báo qua types.FunctionDeclaration + types.Tool.
      - System prompt đặt ở config.system_instruction (không nằm trong messages).
      - Model trả tool call ở resp.function_calls (mỗi cái có .name và .args).
    """

    _MAX_RETRIES = 3
    _MAX_WAIT_SECONDS = 60

    def __init__(self, model: str | None = None):
        from google import genai            # import trễ để không bắt buộc cài đặt
        from google.genai import types
        self._types = types
        self._errors = __import__("google.genai.errors", fromlist=["errors"])
        self.client = genai.Client()
        # Đổi model qua biến GEMINI_MODEL.
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

    def _call_with_retry(self, fn, *args, **kwargs):
        """Gọi Gemini API với auto-retry khi bị rate limit (429) hoặc quá tải (503).

        Free tier giới hạn 5 RPM — khi chạy nhiều test liên tục dễ vượt quota.
        Hàm này đọc retryDelay từ response rồi chờ đúng thời gian đó trước khi
        thử lại, tối đa _MAX_RETRIES lần.
        """
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except self._errors.APIError as e:
                wait = self._parse_retry_delay(e) or (15 * attempt)
                # Hết quota ngày có thể yêu cầu chờ nhiều giờ: báo lỗi ngay thay vì treo.
                if e.code not in (429, 503) or attempt == self._MAX_RETRIES or wait > self._MAX_WAIT_SECONDS:
                    raise
                print(f"  ⏳ Gemini tạm bận ({e.code}) — chờ {wait}s rồi thử lại "
                      f"(lần {attempt}/{self._MAX_RETRIES})...")
                time.sleep(wait)
        raise RuntimeError("Unreachable")  # pragma: no cover

    @staticmethod
    def _parse_retry_delay(err: Exception) -> float | None:
        """Trích retryDelay từ error response của Gemini (vd '59s' -> 60.0)."""
        msg = str(err)
        m = re.search(r"retryDelay.*?(\d+)", msg)
        if m:
            return float(m.group(1)) + 1  # +1s buffer
        return None

    def _tool_config(self, tools_full):
        """Chuyển schema format OpenAI của lab -> FunctionDeclaration của Gemini."""
        types = self._types
        decls = [
            types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"]["description"],
                parameters=t["function"]["parameters"],  # JSON Schema (OpenAPI subset)
            )
            for t in (tools_full or [])
        ]
        return types.Tool(function_declarations=decls)

    def decide(self, system_prompt, user_msg, tool_names, tools_full=None):
        types = self._types
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[self._tool_config(tools_full)] if tools_full else None,
        )
        resp = self._call_with_retry(
            self.client.models.generate_content,
            model=self.model, contents=user_msg, config=config,
        )
        if resp.function_calls:
            return ModelResponse(tool_calls=[
                ToolCall(fc.name, dict(fc.args)) for fc in resp.function_calls
            ])
        return ModelResponse(text=resp.text)

    def summarize_tool_result(self, tool_name, tool_result, user_msg):
        types = self._types
        prompt = (
            f"Câu hỏi người dùng: {user_msg}\n"
            f"Kết quả tool {tool_name}: {json.dumps(tool_result, ensure_ascii=False)}\n"
            'Chỉ dựa trên kết quả tool, trả về JSON đúng 3 field '
            '{"intent": "...", "action": "%s", "reply": "..."} bằng tiếng Việt.' % tool_name
        )
        resp = self._call_with_retry(
            self.client.models.generate_content,
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return ModelResponse(text=resp.text)


class _NvidiaModel:
    """Adapter NVIDIA NIM (format OpenAI) cho TravelBuddy.

    Có cùng bề mặt với _GeminiModel (client.models.generate_content và types của
    Google) nên TravelBuddy và agent.py không cần biết đang chạy provider nào.
    """

    _URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    decide = _GeminiModel.decide
    summarize_tool_result = _GeminiModel.summarize_tool_result

    def __init__(self, model: str | None = None):
        from google.genai import types
        self._types = types
        self._api_key = os.environ.get("NVIDIA_API_KEY")
        if not self._api_key:
            raise ValueError("Thiếu NVIDIA_API_KEY.")
        self.model = model or os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3.5-bolt-e4b")
        self.client = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate_content))

    def _tool_config(self, tools_full):
        types = self._types
        return types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"]["description"],
                parameters_json_schema=t["function"]["parameters"],
            )
            for t in tools_full
        ])

    def _call_with_retry(self, fn, **kwargs):
        return fn(**kwargs)

    @staticmethod
    def _messages(system_prompt: str, contents: list) -> list[dict]:
        """Đổi lịch sử dạng Gemini sang messages dạng OpenAI."""
        messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        call_ids: list[str] = []
        for content in contents:
            parts = content.parts or []
            text = "".join(p.text for p in parts if p.text)
            calls = [p.function_call for p in parts if p.function_call]
            results = [p.function_response for p in parts if p.function_response]
            if content.role == "model":
                call_ids = [f"call_{len(messages)}_{i}" for i in range(len(calls))]
                message = {"role": "assistant", "content": text}
                if calls:
                    message["tool_calls"] = [
                        {"id": call_id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                        for call_id, c in zip(call_ids, calls)
                    ]
                messages.append(message)
            elif results:
                # Harness trả kết quả đúng thứ tự các tool call của lượt model ngay trước.
                messages.extend(
                    {"role": "tool", "tool_call_id": call_id, "name": r.name,
                     "content": json.dumps(r.response, ensure_ascii=False)}
                    for call_id, r in zip(call_ids, results)
                )
            else:
                messages.append({"role": "user", "content": text})
        return messages

    def _generate_content(self, model, contents, config):
        types = self._types
        if isinstance(contents, str):  # decide/summarize của agent.py gửi một chuỗi
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=contents)])]
        body = {
            "model": model,
            "messages": self._messages(config.system_instruction, contents),
            "temperature": 1,
            "top_p": 0.95,
            "max_tokens": 1024,
            "seed": 42,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        if config.tools:
            body["parallel_tool_calls"] = True
            body["tools"] = [
                {"type": "function", "function": {
                    "name": d.name, "description": d.description, "parameters": d.parameters_json_schema,
                }}
                for d in config.tools[0].function_declarations
            ]
        request = urllib.request.Request(self._URL, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                message = json.loads(resp.read().decode())["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"NVIDIA API {e.code}: {e.read().decode(errors='replace')[:300]}") from e

        parts = [
            types.Part(function_call=types.FunctionCall(
                name=call["function"]["name"], args=json.loads(call["function"]["arguments"] or "{}"),
            ))
            for call in message.get("tool_calls") or []
        ]
        if not parts and message.get("content"):
            parts = [types.Part.from_text(text=message["content"])]
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
        )


class _AnthropicModel:
    """Adapter tối giản cho Anthropic Messages (cần `pip install anthropic`)."""

    def __init__(self, model: str = "claude-sonnet-4-5"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def decide(self, system_prompt, user_msg, tool_names, tools_full=None):
        # Anthropic dùng input_schema thay vì parameters — cần convert nếu dùng thật.
        raise NotImplementedError(
            "Ví dụ tham khảo: convert schema OpenAI -> Anthropic (input_schema) "
            "rồi dùng content[i].type == 'tool_use'. Xem slide 'OpenAI vs Anthropic Format'."
        )

    def summarize_tool_result(self, tool_name, tool_result, user_msg):
        raise NotImplementedError
