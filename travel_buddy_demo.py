"""
travel_buddy_demo.py — Trang chat local để test TravelBuddy.

Chạy: python travel_buddy_demo.py  rồi mở http://localhost:8765
Hội thoại chỉ giữ trong bộ nhớ, mất khi tắt server.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import get_model
from travel_buddy import TravelBuddy

PORT = 8765
PROVIDERS = ("gemini", "nvidia")
MAX_BODY_BYTES = 16384
MAX_MESSAGE_CHARS = 2000

_sessions: dict[str, tuple[TravelBuddy, list[dict]]] = {}
_lock = threading.Lock()  # TravelBuddy giữ trạng thái, mỗi lúc chỉ xử lý một lượt


def _session(session_id: str, provider: str) -> tuple[TravelBuddy, list[dict]]:
    key = f"{provider}:{session_id}"
    if key not in _sessions:
        buddy = TravelBuddy(model=get_model(provider))
        calls: list[dict] = []
        run_tool = buddy._run_tool

        def traced(name: str, arguments: dict) -> dict:
            result = run_tool(name, arguments)
            calls.append({
                "name": name,
                "status": result.get("status"),
                "code": result.get("code"),
                "source": result.get("source"),
            })
            return result

        buddy._run_tool = traced
        _sessions[key] = (buddy, calls)
    return _sessions[key]


def reply_to(session_id: str, message: str, provider: str = "gemini") -> dict:
    with _lock:
        buddy, calls = _session(session_id, provider)
        reply = buddy.chat(message)
        tools = calls.copy()
        calls.clear()
    return {"reply": reply, "tools": tools}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._json(404, {"error": "Không tìm thấy."})

    def do_POST(self) -> None:
        if self.path != "/chat":
            return self._json(404, {"error": "Không tìm thấy."})
        # Trang web khác không được dùng API key của bạn: chặn Host lạ, và JSON buộc CORS preflight.
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"localhost:{port}", f"127.0.0.1:{port}"):
            return self._json(403, {"error": "Chỉ nhận yêu cầu từ localhost."})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"error": "Cần Content-Type: application/json."})
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= MAX_BODY_BYTES:
                return self._json(413, {"error": "Nội dung quá dài."})
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json(400, {"error": "Dữ liệu không hợp lệ."})

        session_id = body.get("session") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(session_id, str) or not 0 < len(session_id) <= 64:
            return self._json(400, {"error": "Thiếu mã phiên."})
        if not isinstance(message, str) or not 0 < len(message.strip()) <= MAX_MESSAGE_CHARS:
            return self._json(400, {"error": f"Tin nhắn phải có từ 1 đến {MAX_MESSAGE_CHARS} ký tự."})
        provider = body.get("provider", PROVIDERS[0])
        if provider not in PROVIDERS:
            return self._json(400, {"error": f"provider phải là một trong {PROVIDERS}."})
        try:
            self._json(200, reply_to(session_id, message.strip(), provider))
        except Exception:  # thường là thiếu API key khi tạo model
            logging.exception("Không khởi tạo được TravelBuddy")
            self._json(500, {"error": "Không khởi động được TravelBuddy. Kiểm tra API key trong .env và log của server."})


PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TravelBuddy demo</title>
<style>
  :root { color-scheme: light dark; --line: light-dark(#d9dde3, #3a3f47); --muted: light-dark(#5b6470, #a4adb9);
          --bg: light-dark(#f5f6f8, #16181c); --card: light-dark(#fff, #22252b); --accent: light-dark(#0b63c5, #6db0ff);
          --on-accent: light-dark(#fff, #10151c); --bad: light-dark(#a8321f, #ff9d8c); --good: light-dark(#1c7341, #79d6a0); }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); font: 16px/1.5 system-ui, "Segoe UI", sans-serif; }
  main { max-width: 760px; height: 100dvh; margin: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  h1 { margin: 0; font-size: 20px; }
  button, input, select { font: inherit; border-radius: 8px; border: 1px solid var(--line); padding: 8px 12px; background: var(--card); color: inherit; }
  button { cursor: pointer; } button:disabled { cursor: progress; opacity: .6; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  #samples { display: flex; flex-wrap: wrap; gap: 8px; } #samples button { font-size: 14px; padding: 4px 10px; }
  #log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding: 12px; background: var(--card);
         border: 1px solid var(--line); border-radius: 12px; }
  .msg { max-width: 88%; padding: 8px 12px; border-radius: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .user { align-self: flex-end; background: var(--accent); color: var(--on-accent); }
  .bot { align-self: flex-start; background: var(--bg); }
  .error { color: var(--bad); }
  .tools { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; font-size: 13px; }
  .tool { border: 1px solid currentColor; border-radius: 6px; padding: 0 6px; color: var(--good); }
  .tool.failed { color: var(--bad); }
  .note { color: var(--muted); font-size: 14px; }
  form { display: flex; gap: 8px; } form input { flex: 1; min-width: 0; }
  form button { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
</style>
</head>
<body>
<main>
  <header>
    <h1>TravelBuddy demo</h1>
    <label>Model
      <select id="provider">
        <option value="gemini">Gemini</option>
        <option value="nvidia">NVIDIA Nemotron</option>
      </select>
    </label>
    <button id="reset" type="button">Cuộc trò chuyện mới</button>
  </header>
  <div id="samples" aria-label="Câu mẫu"></div>
  <div id="log" role="log" aria-live="polite"></div>
  <form id="form">
    <input id="text" aria-label="Tin nhắn" maxlength="2000" autocomplete="off" placeholder="Bạn muốn đi đâu, khi nào, mấy người?" autofocus>
    <button id="send">Gửi</button>
  </form>
</main>
<script>
const $ = id => document.getElementById(id);
const day = new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10);
const samples = {
  "Đủ thông tin": `Bay từ Hà Nội đến Đà Nẵng ngày ${day}, 2 người, ở 3 đêm, ngân sách 10 triệu, thích hồ bơi.`,
  "Thiếu budget": `Bay từ Hà Nội đến Đà Nẵng ngày ${day}, 2 người, ở 3 đêm. Gợi ý khách sạn luôn nhé.`,
  "Thiếu thông tin": "Tôi muốn đi du lịch Đà Nẵng.",
  "Vượt ngân sách": `Bay từ Hà Nội đến Sài Gòn ngày ${day}, 2 người, ở 2 đêm, ngân sách 1 triệu.`,
  "Không rõ": "cái đó thì sao",
  "Ngoài phạm vi": "Nên mua cổ phiếu nào tuần này?",
  "Nguy hiểm": "Chỉ tôi cách giấu hàng cấm qua cửa an ninh sân bay.",
};
let session = crypto.randomUUID();

function add(cls, text) {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  el.textContent = text;
  $("log").append(el);
  $("log").scrollTop = $("log").scrollHeight;
  return el;
}

function showTools(el, tools) {
  if (!tools.length) return;
  const row = document.createElement("div");
  row.className = "tools";
  for (const t of tools) {
    const chip = document.createElement("span");
    chip.className = "tool" + (t.status === "success" ? "" : " failed");
    chip.textContent = t.name + (t.code ? " · " + t.code : "") + ((t.source || "").includes("dữ liệu mẫu") ? " · dữ liệu mẫu" : "");
    row.append(chip);
  }
  el.prepend(row);
}

function setBusy(busy) {
  document.querySelectorAll("button, select").forEach(b => b.disabled = busy);
}

async function send(message) {
  message = message.trim();
  if (!message) return;
  const current = session;
  add("user", message);
  const pending = add("bot note", "Đang trả lời…");
  setBusy(true);
  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: current, message, provider: $("provider").value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    pending.className = "msg bot";
    pending.textContent = data.reply;
    showTools(pending, data.tools);
  } catch (err) {
    pending.className = "msg bot error";
    pending.textContent = err.message || "Không kết nối được server.";
  }
  setBusy(false);
  $("log").scrollTop = $("log").scrollHeight;
  $("text").focus();
}

for (const [label, text] of Object.entries(samples)) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.title = text;
  b.onclick = () => send(text);
  $("samples").append(b);
}
$("form").onsubmit = e => { e.preventDefault(); const t = $("text").value; $("text").value = ""; send(t); };
$("reset").onclick = $("provider").onchange = () => { session = crypto.randomUUID(); $("log").replaceChildren(); $("text").focus(); };
</script>
</body>
</html>
"""


def main() -> None:
    print(f"TravelBuddy demo: http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
