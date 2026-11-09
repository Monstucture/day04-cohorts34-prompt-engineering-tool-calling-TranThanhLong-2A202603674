"""
tools.py — Các tool của TravelBuddy, dùng theo thứ tự:

  Tool 1: search_flights             -> chuyến bay và giá.
  Tool 2: calculate_budget_per_night -> ngân sách còn lại mỗi đêm.
  Tool 3: search_hotels              -> khách sạn trong ngân sách.

Bám sát "Tool Schema Anatomy" và "Tool Return Format Best Practices":
  - description (lấy từ docstring của hàm) = tài liệu cho model: (1) chức năng, (2) KHI NÀO dùng, (3) khi nào KHÔNG dùng.
  - parameters mô tả bằng JSON Schema, có `required`, dùng `enum` + ví dụ để giảm lỗi arguments.
  - Mọi tool TRẢ VỀ JSON có cấu trúc:
        success -> {"status": "success", "data": {...}, "source": "..."}
        error   -> {"status": "error", "message": "...", "code": "..."}
    (Model xử lý structured JSON tốt hơn nhiều so với raw text/HTML.)

Nguyên tắc thiết kế tool: Single Responsibility, Idempotency, Granularity hợp lý,
Test độc lập. Mỗi tool ở đây làm ĐÚNG một việc nghiệp vụ và test được mà không cần agent.
"""

from __future__ import annotations

import http.client
import inspect
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

def _normalize_city(city: str) -> str:
    """Chuẩn hoá tên thành phố: bỏ dấu cách thừa, lowercase, gộp vài alias."""
    c = " ".join(city.strip().lower().split())
    aliases = {
        "ha noi": "hanoi",
        "hà nội": "hanoi",
        "hanoi": "hanoi",
        "sài gòn": "ho chi minh",
        "saigon": "ho chi minh",
        "tphcm": "ho chi minh",
        "hcm": "ho chi minh",
        "hồ chí minh": "ho chi minh",
        "đà nẵng": "da nang",
        "danang": "da nang",
        "huế": "hue",
    }
    return aliases.get(c, c)


# ---------------------------------------------------------------------------
# TOOL 1 — API WRAPPER: search_flights(origin, destination, departure_date, travelers)
# ---------------------------------------------------------------------------
# Giá + lịch bay: SerpApi Google Flights (đặt SERPAPI_API_KEY).
# Cổng/nhà ga + giờ đến cập nhật: AirLabs (đặt AIRLABS_API_KEY, tuỳ chọn) vì Google
# Flights không có cổng. Không có SERPAPI_API_KEY -> chạy dữ liệu mẫu offline.

# (hãng, mã, giờ đi, giờ đến, giá/người VND, ga đi, cổng đi, ga đến, cổng đến)
_FAKE_FLIGHTS = {
    ("HAN", "SGN"): [
        ("Vietnam Airlines", "VN 213", "06:00", "08:10", 2150000, "T1", "12", "T1", "5"),
        ("Vietjet Air", "VJ 133", "09:30", "11:40", 1490000, "T1", "24", "T1", "9"),
    ],
    ("SGN", "HAN"): [
        ("Bamboo Airways", "QH 202", "14:15", "16:25", 1720000, "T1", "7", "T1", "18"),
    ],
    ("HAN", "DAD"): [
        ("Vietnam Airlines", "VN 157", "07:45", "09:05", 1280000, "T1", "3", "T1", "2"),
    ],
}


def _positive_int(value) -> int | None:
    """Nhận cả 2.0 vì model thường gửi số nguyên dưới dạng float."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not number.is_integer() or number < 1:
        return None
    return int(number)


def _is_amount(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _http_json(base_url: str, params: dict, timeout: float) -> dict:
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (OSError, http.client.HTTPException) as e:
        # urlopen không bọc lỗi khi đọc phản hồi, ví dụ server đóng kết nối giữa chừng.
        raise urllib.error.URLError(type(e).__name__) from e


def _airport_stop(airport: str, time: str | None, terminal=None, gate=None) -> dict:
    return {"airport": airport, "time": time, "estimated_time": time, "terminal": terminal, "gate": gate}


def _fake_flights(origin: str, destination: str, departure_date: str, travelers: int) -> list[dict]:
    return [
        {
            "total_price": price * travelers,
            "currency": "VND",
            "total_duration_minutes": None,
            "segments": [{
                "airline": airline,
                "flight_number": number,
                "departure": _airport_stop(origin, f"{departure_date} {dep}", dep_terminal, dep_gate),
                "arrival": _airport_stop(destination, f"{departure_date} {arr}", arr_terminal, arr_gate),
            }],
        }
        for airline, number, dep, arr, price, dep_terminal, dep_gate, arr_terminal, arr_gate
        in _FAKE_FLIGHTS.get((origin, destination), [])
    ]


def _add_gate_info(segment: dict, api_key: str, timeout: float) -> None:
    """Bổ sung cổng/nhà ga; lỗi tra cứu không được làm mất kết quả giá vé."""
    try:
        payload = _http_json(
            "https://airlabs.co/api/v9/flight",
            {"flight_iata": segment["flight_number"].replace(" ", ""), "api_key": api_key},
            timeout,
        )
    except (urllib.error.URLError, TimeoutError, ValueError):
        return
    # Tài liệu AirLabs minh hoạ dữ liệu phẳng, còn API có thể bọc trong "response".
    info = payload.get("response") or payload
    # AirLabs trả về chuyến hiện hành theo số hiệu, có thể là ngày khác với vé đang tìm.
    if not str(info.get("dep_time", "")).startswith(segment["departure"]["time"][:10]):
        return
    segment["departure"].update(terminal=info.get("dep_terminal"), gate=info.get("dep_gate"))
    segment["arrival"].update(terminal=info.get("arr_terminal"), gate=info.get("arr_gate"))
    if info.get("arr_estimated"):
        segment["arrival"]["estimated_time"] = info["arr_estimated"]


def _real_flights(
    origin: str, destination: str, departure_date: date, travelers: int, api_key: str, timeout: float
) -> list[dict]:
    payload = _http_json(
        "https://serpapi.com/search.json",
        {
            "engine": "google_flights",
            "type": 2,  # một chiều
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": departure_date.isoformat(),
            "adults": travelers,
            "currency": "VND",
            "hl": "vi",
            "api_key": api_key,
        },
        timeout,
    )
    error = payload.get("error")
    if error and "returned any results" not in error:
        raise ValueError(error)

    flights = [
        {
            "total_price": item.get("price"),
            "currency": "VND",
            "total_duration_minutes": item.get("total_duration"),
            "segments": [
                {
                    "airline": leg["airline"],
                    "flight_number": leg["flight_number"],
                    "departure": _airport_stop(leg["departure_airport"]["id"], leg["departure_airport"]["time"]),
                    "arrival": _airport_stop(leg["arrival_airport"]["id"], leg["arrival_airport"]["time"]),
                }
                for leg in item["flights"]
            ],
        }
        for item in payload.get("best_flights", []) + payload.get("other_flights", [])
    ]

    # Giới hạn trước khi tra cổng vì mỗi chặng là một lượt gọi AirLabs; chuyến thiếu giá xếp cuối.
    flights = sorted(flights, key=lambda f: (f["total_price"] is None, f["total_price"] or 0))[:10]

    gate_key = os.environ.get("AIRLABS_API_KEY")
    # Cổng chỉ có sát giờ bay; bỏ qua ngày xa để khỏi tốn quota AirLabs.
    if gate_key and departure_date <= date.today() + timedelta(days=1):
        for flight in flights:
            for segment in flight["segments"]:
                _add_gate_info(segment, gate_key, timeout)
    return flights


def search_flights(
    origin: str, destination: str, departure_date: str, travelers: int, timeout: float = 20.0
) -> dict:
    """Tìm chuyến bay MỘT CHIỀU và trả về danh sách chuyến kèm giá.

    Dùng KHI khách muốn tìm chuyến bay/giá vé và đã nói rõ điểm đi, điểm đến, ngày
    bay, số hành khách. KHÔNG dùng để đặt vé, tìm vé khứ hồi, hay khi còn thiếu thông tin.

    Args:
        origin: Mã IATA sân bay đi, ví dụ 'HAN'.
        destination: Mã IATA sân bay đến, ví dụ 'DAD'.
        departure_date: Ngày bay YYYY-MM-DD, không ở quá khứ.
        travelers: Số hành khách người lớn, từ 1 đến 9.

    Returns:
        Thành công: status='success', data.flights có tối đa 10 chuyến. Mỗi chuyến có
        total_price (đã gồm mọi hành khách), currency, total_duration_minutes và segments.
        Mỗi segment có airline, flight_number, departure và arrival; hai trường này gồm
        airport, time, estimated_time, terminal, gate (terminal/gate null khi chưa công bố).
        Thất bại: status='error' với code INVALID_ARGUMENT, UPSTREAM_ERROR hoặc NO_FLIGHTS.
    """
    def invalid(message: str) -> dict:
        return {"status": "error", "message": message, "code": "INVALID_ARGUMENT"}

    origin = str(origin or "").strip().upper()
    destination = str(destination or "").strip().upper()
    for code in (origin, destination):
        if len(code) != 3 or not code.isascii() or not code.isalpha():
            return invalid("origin và destination phải là mã sân bay IATA 3 chữ cái, ví dụ 'HAN'.")
    if origin == destination:
        return invalid("origin và destination phải khác nhau.")

    try:
        parsed_date = datetime.strptime(departure_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return invalid("departure_date phải có định dạng YYYY-MM-DD.")
    if parsed_date < date.today():
        return invalid("departure_date không được ở quá khứ.")

    count = _positive_int(travelers)
    if count is None or count > 9:
        return invalid("travelers phải là số nguyên từ 1 đến 9.")

    api_key = os.environ.get("SERPAPI_API_KEY")
    if api_key:
        try:
            flights = _real_flights(origin, destination, parsed_date, count, api_key, timeout)
        except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError) as e:
            # Không fallback sang dữ liệu mẫu: giá giả nguy hiểm hơn một lỗi rõ ràng.
            # Chỉ nêu loại lỗi: thông báo gốc có thể chứa URL kèm API key.
            return {
                "status": "error",
                "message": f"Không tra cứu được chuyến bay ({type(e).__name__}).",
                "code": "UPSTREAM_ERROR",
            }
        source = "serpapi.com (Google Flights) + airlabs.co"
    else:
        flights = _fake_flights(origin, destination, parsed_date.isoformat(), count)
        source = "fake-flight-backend (offline, dữ liệu mẫu)"

    if not flights:
        return {
            "status": "error",
            "message": f"Không tìm thấy chuyến bay {origin} -> {destination} ngày {parsed_date}.",
            "code": "NO_FLIGHTS",
        }
    return {
        "status": "success",
        "data": {
            "origin": origin,
            "destination": destination,
            "departure_date": parsed_date.isoformat(),
            "travelers": count,
            "flights": flights,
        },
        "source": source,
    }


SEARCH_FLIGHTS_SCHEMA = {  # Format OpenAI; Gemini adapter convert ở llm.py
    "type": "function",
    "function": {
        "name": "search_flights",
        "description": inspect.cleandoc(search_flights.__doc__),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Mã IATA sân bay đi, ví dụ 'HAN'."},
                "destination": {"type": "string", "description": "Mã IATA sân bay đến, ví dụ 'SGN'."},
                "departure_date": {"type": "string", "description": "Ngày bay YYYY-MM-DD, ví dụ '2026-12-20'."},
                "travelers": {"type": "integer", "description": "Số hành khách người lớn, từ 1 đến 9."},
            },
            "required": ["origin", "destination", "departure_date", "travelers"],
        },
    },
}


# ---------------------------------------------------------------------------
# TOOL 2 — TÍNH TOÁN: calculate_budget_per_night(...)
# ---------------------------------------------------------------------------
def calculate_budget_per_night(
    total_budget: float, nights: int, cheapest_flights_total: float, destination: str, travelers: int
) -> dict:
    """Tính ngân sách còn lại sau khi trừ vé máy bay, chia theo đêm và theo khách.

    Dùng KHI khách muốn biết còn bao nhiêu tiền cho mỗi đêm và đã có tổng ngân sách,
    số đêm, tổng tiền vé. KHÔNG dùng để tìm vé hay khách sạn, và KHÔNG tự đoán ngân
    sách hay tiền vé khi chưa có.

    Args:
        total_budget: Tổng ngân sách của cả nhóm, cùng đơn vị tiền với tiền vé.
        nights: Số đêm lưu trú, từ 1 trở lên.
        cheapest_flights_total: total_price nhỏ nhất từ search_flights (không nhân thêm).
        destination: Điểm đến, ví dụ 'Đà Nẵng'.
        travelers: Số hành khách, từ 1 trở lên.

    Returns:
        Thành công: status='success', data gồm remaining_budget, remaining_per_night,
        remaining_per_night_per_traveler và within_budget (false nghĩa là vé đã vượt
        ngân sách, các số còn lại mang giá trị âm).
        Thất bại: status='error' với code INVALID_ARGUMENT.
    """
    def invalid(message: str) -> dict:
        return {"status": "error", "message": message, "code": "INVALID_ARGUMENT"}

    if not _is_amount(total_budget) or total_budget <= 0:
        return invalid("total_budget phải là số lớn hơn 0.")
    if not _is_amount(cheapest_flights_total) or cheapest_flights_total < 0:
        return invalid("cheapest_flights_total phải là số không âm.")
    night_count = _positive_int(nights)
    if night_count is None:
        return invalid("nights phải là số nguyên từ 1 trở lên.")
    traveler_count = _positive_int(travelers)
    if traveler_count is None:
        return invalid("travelers phải là số nguyên từ 1 trở lên.")
    if not isinstance(destination, str) or not destination.strip():
        return invalid("Thiếu tham số 'destination'.")

    remaining = total_budget - cheapest_flights_total
    per_night = remaining / night_count
    return {
        "status": "success",
        "data": {
            "destination": destination.strip(),
            "nights": night_count,
            "travelers": traveler_count,
            "total_budget": total_budget,
            "cheapest_flights_total": cheapest_flights_total,
            "remaining_budget": round(remaining, 2),
            "remaining_per_night": round(per_night, 2),
            "remaining_per_night_per_traveler": round(per_night / traveler_count, 2),
            "within_budget": remaining >= 0,
        },
        "source": "calculated",
    }


CALCULATE_BUDGET_PER_NIGHT_SCHEMA = {  # Format OpenAI; Gemini adapter convert ở llm.py
    "type": "function",
    "function": {
        "name": "calculate_budget_per_night",
        "description": inspect.cleandoc(calculate_budget_per_night.__doc__),
        "parameters": {
            "type": "object",
            "properties": {
                "total_budget": {
                    "type": "number",
                    "description": "Tổng ngân sách của cả nhóm, cùng đơn vị tiền với tiền vé.",
                },
                "nights": {"type": "integer", "description": "Số đêm lưu trú, từ 1 trở lên."},
                "cheapest_flights_total": {
                    "type": "number",
                    "description": (
                        "Tổng tiền vé rẻ nhất của cả nhóm: total_price nhỏ nhất từ "
                        "search_flights (đã gồm mọi hành khách, không nhân thêm)."
                    ),
                },
                "destination": {"type": "string", "description": "Điểm đến, ví dụ 'Đà Nẵng' hoặc 'DAD'."},
                "travelers": {"type": "integer", "description": "Số hành khách, từ 1 trở lên."},
            },
            "required": ["total_budget", "nights", "cheapest_flights_total", "destination", "travelers"],
        },
    },
}


# ---------------------------------------------------------------------------
# TOOL 3 — API WRAPPER: search_hotels(city, max_price_per_night, preferences)
# ---------------------------------------------------------------------------
# Dùng chung SERPAPI_API_KEY (engine Google Hotels); không có key -> dữ liệu mẫu offline.

# key đã chuẩn hoá -> (tên, loại hình, giá/đêm VND, điểm đánh giá, tiện ích)
_FAKE_HOTELS = {
    "hanoi": [
        ("Khách sạn Mẫu Hồ Tây", "hotel", 950000, 4.3, ["wifi", "bữa sáng miễn phí"]),
        ("Khách sạn Mẫu Phố Cổ", "hotel", 1600000, 4.6, ["wifi", "hồ bơi", "bữa sáng miễn phí"]),
    ],
    "ho chi minh": [
        ("Khách sạn Mẫu Quận 1", "hotel", 1350000, 4.4, ["wifi", "hồ bơi", "đỗ xe"]),
        ("Nhà nghỉ Mẫu Bến Thành", "hostel", 480000, 4.0, ["wifi"]),
    ],
    "da nang": [
        ("Khách sạn Mẫu Mỹ Khê", "hotel", 1200000, 4.5, ["wifi", "gần biển", "hồ bơi"]),
        ("Nhà nghỉ Mẫu Sông Hàn", "hostel", 550000, 4.1, ["wifi", "đỗ xe"]),
    ],
}


def _fake_hotels(city: str, preferences: list[str]) -> list[dict]:
    wanted = [p.strip().lower() for p in preferences]
    return [
        {"name": name, "type": kind, "price_per_night": price, "currency": "VND",
         "overall_rating": rating, "reviews": None, "amenities": amenities}
        for name, kind, price, rating, amenities in _FAKE_HOTELS.get(_normalize_city(city), [])
        if all(any(p in amenity for amenity in amenities) for p in wanted)
    ]


def _real_hotels(
    city: str, max_price: float, preferences: list[str], check_in: date, check_out: date, api_key: str, timeout: float
) -> list[dict]:
    payload = _http_json(
        "https://serpapi.com/search.json",
        {
            "engine": "google_hotels",
            # shortcut: sở thích chỉ đưa vào từ khoá nên không đảm bảo khớp, muốn lọc chắc thì map sang mã amenities của SerpApi.
            "q": " ".join(["khách sạn", city, *preferences]),
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "max_price": math.floor(max_price),
            "currency": "VND",
            "hl": "vi",
            "gl": "vn",
            "api_key": api_key,
        },
        timeout,
    )
    error = payload.get("error")
    if error and "returned any results" not in error:
        raise ValueError(error)
    return [
        {
            "name": item["name"],
            "type": item.get("type"),
            "price_per_night": (item.get("rate_per_night") or {}).get("extracted_lowest"),
            "currency": "VND",
            "overall_rating": item.get("overall_rating"),
            "reviews": item.get("reviews"),
            "amenities": item.get("amenities", []),
        }
        for item in payload.get("properties", [])
    ]


def search_hotels(
    city: str,
    max_price_per_night: float,
    preferences: list[str] | None = None,
    check_in_date: str | None = None,
    check_out_date: str | None = None,
    timeout: float = 20.0,
) -> dict:
    """Tìm khách sạn tại MỘT thành phố có giá mỗi đêm không vượt ngân sách, rẻ nhất trước.

    Dùng KHI khách muốn tìm khách sạn và đã nói rõ thành phố cùng mức giá tối đa mỗi
    đêm. KHÔNG dùng để đặt phòng, hay khi còn thiếu thành phố hoặc ngân sách.

    Args:
        city: Tên thành phố, ví dụ 'Đà Nẵng'.
        max_price_per_night: Giá tối đa mỗi đêm (VND), thường là remaining_per_night.
        preferences: Sở thích (tuỳ chọn), ví dụ ['hồ bơi', 'gần biển'].
        check_in_date: Ngày nhận phòng YYYY-MM-DD (tuỳ chọn, mặc định ngày mai).
        check_out_date: Ngày trả phòng YYYY-MM-DD (tuỳ chọn, mặc định sau nhận phòng 1 đêm).

    Returns:
        Thành công: status='success', data gồm check_in_date, check_out_date và hotels
        (tối đa 10), mỗi khách sạn có name, type, price_per_night, currency,
        overall_rating, reviews, amenities.
        Thất bại: status='error' với code INVALID_ARGUMENT, UPSTREAM_ERROR hoặc NO_HOTELS.
    """
    def invalid(message: str) -> dict:
        return {"status": "error", "message": message, "code": "INVALID_ARGUMENT"}

    if not isinstance(city, str) or not city.strip():
        return invalid("Thiếu tham số 'city'.")
    city = city.strip()
    if not _is_amount(max_price_per_night) or max_price_per_night <= 0:
        return invalid("max_price_per_night phải là số lớn hơn 0.")
    preferences = preferences or []
    if not isinstance(preferences, list) or not all(isinstance(p, str) and p.strip() for p in preferences):
        return invalid("preferences phải là danh sách chuỗi, ví dụ ['hồ bơi', 'gần biển'].")

    try:
        check_in = (
            datetime.strptime(check_in_date, "%Y-%m-%d").date()
            if check_in_date else date.today() + timedelta(days=1)
        )
        check_out = (
            datetime.strptime(check_out_date, "%Y-%m-%d").date()
            if check_out_date else check_in + timedelta(days=1)
        )
    except (TypeError, ValueError):
        return invalid("check_in_date và check_out_date phải có định dạng YYYY-MM-DD.")
    if check_in < date.today():
        return invalid("check_in_date không được ở quá khứ.")
    if check_out <= check_in:
        return invalid("check_out_date phải sau check_in_date.")

    api_key = os.environ.get("SERPAPI_API_KEY")
    if api_key:
        try:
            hotels = _real_hotels(city, max_price_per_night, preferences, check_in, check_out, api_key, timeout)
        except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError) as e:
            # Không trả dữ liệu mẫu khi API thật lỗi; chỉ nêu loại lỗi để không lộ API key.
            return {
                "status": "error",
                "message": f"Không tra cứu được khách sạn ({type(e).__name__}).",
                "code": "UPSTREAM_ERROR",
            }
        source = "serpapi.com (Google Hotels)"
    else:
        hotels = _fake_hotels(city, preferences)
        source = "fake-hotel-backend (offline, dữ liệu mẫu)"

    # Lọc lại ở đây vì không thể xác nhận khách sạn thiếu giá có nằm trong ngân sách hay không.
    hotels = sorted(
        (h for h in hotels if h["price_per_night"] is not None and h["price_per_night"] <= max_price_per_night),
        key=lambda h: h["price_per_night"],
    )
    if not hotels:
        return {
            "status": "error",
            "message": f"Không tìm thấy khách sạn tại {city} trong mức {max_price_per_night:,.0f}/đêm.",
            "code": "NO_HOTELS",
        }
    return {
        "status": "success",
        "data": {
            "city": city,
            "max_price_per_night": max_price_per_night,
            "preferences": preferences,
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "hotels": hotels[:10],
        },
        "source": source,
    }


SEARCH_HOTELS_SCHEMA = {  # Format OpenAI; Gemini adapter convert ở llm.py
    "type": "function",
    "function": {
        "name": "search_hotels",
        "description": inspect.cleandoc(search_hotels.__doc__),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Tên thành phố, ví dụ 'Đà Nẵng'."},
                "max_price_per_night": {
                    "type": "number",
                    "description": (
                        "Giá tối đa mỗi đêm (VND), thường là remaining_per_night từ "
                        "calculate_budget_per_night."
                    ),
                },
                "preferences": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sở thích (tuỳ chọn), ví dụ ['hồ bơi', 'gần biển'].",
                },
                "check_in_date": {
                    "type": "string",
                    "description": "Ngày nhận phòng YYYY-MM-DD (tuỳ chọn, mặc định ngày mai).",
                },
                "check_out_date": {
                    "type": "string",
                    "description": "Ngày trả phòng YYYY-MM-DD (tuỳ chọn, mặc định sau nhận phòng 1 đêm).",
                },
            },
            "required": ["city", "max_price_per_night"],
        },
    },
}


# ---------------------------------------------------------------------------
# TOOL REGISTRY — nối tên tool -> (schema, hàm thực thi)
# ---------------------------------------------------------------------------
TOOLS = {
    "search_flights": {"schema": SEARCH_FLIGHTS_SCHEMA, "fn": search_flights},
    "calculate_budget_per_night": {"schema": CALCULATE_BUDGET_PER_NIGHT_SCHEMA, "fn": calculate_budget_per_night},
    "search_hotels": {"schema": SEARCH_HOTELS_SCHEMA, "fn": search_hotels},
}


def tool_schemas() -> list[dict]:
    """Danh sách schema để truyền cho model qua tham số `tools`."""
    return [t["schema"] for t in TOOLS.values()]


def execute_tool(name: str, arguments: dict) -> dict:
    """Application thực thi tool call và trả về JSON kết quả."""
    if name not in TOOLS:
        # "Xử lý Tool Errors": tool not found -> log + return error JSON.
        return {"status": "error", "message": f"Tool '{name}' không tồn tại.", "code": "TOOL_NOT_FOUND"}
    try:
        return TOOLS[name]["fn"](**arguments)
    except TypeError as e:
        # arguments sai/thiếu so với chữ ký hàm -> lỗi thuộc loại TOOL SCHEMA.
        return {"status": "error", "message": f"Arguments không hợp lệ: {e}", "code": "BAD_ARGUMENTS"}


if __name__ == "__main__":
    # Test độc lập từng tool TRƯỚC khi gắn vào agent (nguyên tắc 'Test độc lập').
    print("search_flights ->", json.dumps(search_flights("HAN", "DAD", "2099-01-01", 2), ensure_ascii=False))
    print("calculate_budget ->", json.dumps(calculate_budget_per_night(10000000, 3, 2560000, "DAD", 2), ensure_ascii=False))
    print("search_hotels ->", json.dumps(search_hotels("Đà Nẵng", 2480000), ensure_ascii=False))
