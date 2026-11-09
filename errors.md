# Ghi chú lỗi — Prompt / Tool schema / Control flow (Task 5)

> Câu hỏi mở đầu buổi học: *"Cùng một agent, đôi khi gọi tool đúng, đôi khi sai —
> do prompt hay do tool?"*
> Phân loại đúng **nhóm lỗi** mới sửa đúng chỗ. Chạy `python demo_errors.py` để
> tái hiện cả 3 lỗi dưới đây (mỗi lỗi: SYMPTOM → DIAGNOSIS → FIX).

| # | Nhóm lỗi | Dấu hiệu (symptom) | Nguyên nhân gốc | Cách sửa |
|---|----------|--------------------|-----------------|----------|
| 1 | **PROMPT** | Hỏi giá bitcoin → agent **bịa** "tăng ~5%" thay vì từ chối | System prompt mơ hồ, thiếu `Constraints`, còn ép *"make your best guess"* | Thêm ràng buộc `NEVER: lời khuyên tài chính` + quy tắc từ chối khi ngoài phạm vi |
| 2 | **TOOL SCHEMA** | `search_flights(origin="Hà Nội", ...)` → `INVALID_ARGUMENT` | Schema `origin` **thiếu mô tả định dạng/ví dụ** → model đoán giá trị theo ngôn ngữ người dùng | Mô tả rõ `"Mã IATA sân bay đi, ví dụ 'HAN'"` để model truyền đúng mã |
| 3 | **CONTROL FLOW** | Gọi tool xong nhưng câu trả lời **rỗng** | Vòng lặp agent **quên bước 4** (feed tool result trở lại model) | Sau khi execute tool, luôn gọi lại model với kết quả rồi mới trả lời |

## Chi tiết & cách phân biệt

### 1. Lỗi PROMPT
- **Bằng chứng:** giữ nguyên tool + vòng lặp, chỉ đổi `BROKEN_SYSTEM_PROMPT`
  ↔ `SYSTEM_PROMPT`. Prompt kém → bịa; prompt tốt → từ chối. ⇒ lỗi nằm ở **prompt**.
- **Bám slide:** *System Prompt Anti-Patterns* (mơ hồ, thiếu boundary) và
  *Negative Prompting* (phải nói rõ nên làm gì thay vì chỉ nói "đừng").
- **Cách nhận diện chung:** hành vi sai nhưng tool chạy đúng khi test độc lập, và
  đổi câu chữ prompt là hành vi đổi theo.

### 2. Lỗi TOOL SCHEMA
- **Bằng chứng:** hàm `search_flights("HAN", ...)` chạy đúng khi test độc lập; chỉ hỏng
  khi **arguments** sai giá trị. Root cause là mô tả schema, không phải logic hàm.
- **Bám slide:** *Tool Schema Anatomy*, *Parameter Design Best Practices*
  (dùng `enum`, ví dụ trong description để giảm lỗi arguments) và
  *Good vs Bad Tool Description*.
- **Các biến thể thường gặp:** description quá ngắn ("gets weather") → model không
  biết khi nào dùng; thiếu `required` → model gọi khi chưa đủ dữ liệu; enum thiếu →
  sai giá trị; hai tool mô tả chồng lấn → model chọn nhầm tool.

### 3. Lỗi CONTROL FLOW
- **Bằng chứng:** `run_agent_broken()` gọi đúng tool, nhận đúng kết quả, nhưng
  **không feed lại** cho model ⇒ câu trả lời rỗng. `run_agent()` chuẩn thì đúng.
- **Bám slide:** *Tool Calling Flow* (4 bước) và *Tool calling là bài toán control
  flow*: khi nào gọi, gọi cái gì, theo thứ tự nào, và làm gì khi tool fail.
- **Các biến thể thường gặp:** quên feed result; thiếu `max_steps` → loop vô hạn;
  không xử lý `status="error"` từ tool → agent treo hoặc trả lời sai;
  retry im lặng quá nhiều lần (slide *Xử lý Tool Errors*: không retry quá 2 lần).

## Bảng chẩn đoán nhanh "do prompt hay do tool?"

```
Tool chạy đúng khi test độc lập?
├─ KHÔNG → sửa implementation của tool (bug code, không thuộc 3 nhóm trên)
└─ CÓ → Model truyền arguments đúng không?
        ├─ SAI arguments / gọi nhầm tool   → LỖI TOOL SCHEMA (sửa description/enum/required)
        └─ ĐÚNG → Kết quả có được đưa lại model & trả lời cuối không?
                  ├─ KHÔNG → LỖI CONTROL FLOW (sửa vòng lặp)
                  └─ CÓ nhưng nội dung/ hành vi sai → LỖI PROMPT (sửa rules/constraints/format)
```
