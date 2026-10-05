"""Draft the ADR-0149 action expansion: 30 bilingual tool-use items -> tasks/_src/tool-use-expansion.json.

Every item is deterministic (json_match). Formats, the reference date and call order are stated in
the prompt so that exactly one answer is correct. Status: DRAFT, not yet cross-reviewed.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tasks/_src/tool-use-expansion.json"

KO_HEAD = ("너는 도구를 호출하는 에이전트야. 응답은 JSON 객체 하나만 출력해. 형식은 "
           "{\"tool\": 도구이름 또는 null, \"arguments\": {…}}. 설명·인사·코드블록 표시 금지.")
EN_HEAD = ("You are a tool-calling agent. Output exactly one JSON object and nothing else, shaped "
           "{\"tool\": <name or null>, \"arguments\": {…}}. No prose, greeting, or code fence.")
KO_MULTI = ("너는 도구를 호출하는 에이전트야. 응답은 JSON 객체 하나만 출력해. 형식은 "
            "{\"calls\": [{\"tool\": 도구이름, \"arguments\": {…}}, …]}. 실행 순서대로 나열하고, "
            "같은 도구를 여러 번 부를 때는 사용자가 말한 순서를 따른다. 설명·인사·코드블록 표시 금지.")
EN_MULTI = ("You are a tool-calling agent. Output exactly one JSON object and nothing else, shaped "
            "{\"calls\": [{\"tool\": <name>, \"arguments\": {…}}, …]}. List the calls in execution order; "
            "repeated calls to one tool follow the order the user mentioned them. No prose, greeting, or code fence.")
KO_NONE = "요청을 처리할 도구가 없으면 {\"tool\": null, \"arguments\": {}} 를 출력해."
EN_NONE = "If no tool can serve the request, output {\"tool\": null, \"arguments\": {}}."
KO_TODAY = "오늘은 2026-10-05(월)이다. 연도가 없는 날짜는 오늘 이후 가장 가까운 날짜로 해석한다. 날짜는 YYYY-MM-DD, 시각은 24시간제 HH:MM으로 쓴다."
EN_TODAY = "Today is 2026-10-05 (Monday). A date without a year means its next occurrence on or after today. Write dates as YYYY-MM-DD and times as 24-hour HH:MM."
KO_NEXT = "지금 실행할 다음 도구 하나만 출력해. 목표가 이미 달성됐으면 {\"tool\": null, \"arguments\": {}} 를 출력해."
EN_NEXT = "Output only the next tool to run now. If the goal is already met, output {\"tool\": null, \"arguments\": {}}."


def tools(lines):
    return "\n".join(f"- {line}" for line in lines)


def single(head, tools_ko, tools_en, extra_ko="", extra_en=""):
    ko = f"{KO_HEAD}\n\n{extra_ko + chr(10) + chr(10) if extra_ko else ''}쓸 수 있는 도구:\n{tools(tools_ko)}\n\n{KO_NONE}"
    en = f"{EN_HEAD}\n\n{extra_en + chr(10) + chr(10) if extra_en else ''}Available tools:\n{tools(tools_en)}\n\n{EN_NONE}"
    return ko, en


def multi(tools_ko, tools_en, extra_ko="", extra_en=""):
    ko = f"{KO_MULTI}\n\n{extra_ko + chr(10) + chr(10) if extra_ko else ''}쓸 수 있는 도구:\n{tools(tools_ko)}"
    en = f"{EN_MULTI}\n\n{extra_en + chr(10) + chr(10) if extra_en else ''}Available tools:\n{tools(tools_en)}"
    return ko, en


def agent(tools_ko, tools_en, policy_ko, policy_en):
    ko = f"{KO_HEAD}\n\n규칙: {policy_ko}\n\n쓸 수 있는 도구:\n{tools(tools_ko)}\n\n{KO_NEXT}"
    en = f"{EN_HEAD}\n\nPolicy: {policy_en}\n\nAvailable tools:\n{tools(tools_en)}\n\n{EN_NEXT}"
    return ko, en


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


ITEMS = []


def add(id_, subcategory, prior, systems, turns, expected, subset=False, length="short"):
    ko_sys, en_sys = systems
    ko_turn, en_turn = turns
    ko_exp, en_exp = expected if isinstance(expected, tuple) else (expected, expected)
    check = lambda e: {"type": "json_match", "expected": e, **({"subset": True} if subset else {})}
    ITEMS.append({
        "id": id_, "pair_id": id_, "track": "work", "block": "code", "category": "tool-use-x",
        "subcategory": subcategory, "difficulty_prior": prior, "mode": "chat", "language": None,
        "max_tokens": 1024, "scoring": "deterministic", "split": "dev", "source": "authored",
        "length_band": length,
        "ko": {"system": ko_sys, "turns": [ko_turn], "check": check(ko_exp)},
        "en": {"system": en_sys, "turns": [en_turn], "check": check(en_exp)},
    })


# ---- single tool, simple ---------------------------------------------------------------
add("actx-single-01", "single-tool", "easy",
    single(KO_HEAD, ["get_balance(account_id: string) — 계좌 잔액 조회", "transfer(from_account: string, to_account: string, amount_krw: number) — 송금"],
           ["get_balance(account_id: string) — look up an account balance", "transfer(from_account: string, to_account: string, amount_krw: number) — send money"]),
    ("계좌 110-234-5567 잔액 알려줘.", "What's the balance on account 110-234-5567?"),
    call("get_balance", account_id="110-234-5567"))
add("actx-single-02", "single-tool", "easy",
    single(KO_HEAD, ["create_event(title: string, date: string, start_time: string) — 일정 등록", "list_events(date: string) — 하루 일정 조회"],
           ["create_event(title: string, date: string, start_time: string) — add a calendar event", "list_events(date: string) — list one day's events"],
           KO_TODAY, EN_TODAY),
    ("10월 8일 오후 3시에 '분기 리뷰' 일정 잡아 줘.", "Put 'Quarterly review' on my calendar for October 8 at 3 pm."),
    (call("create_event", title="분기 리뷰", date="2026-10-08", start_time="15:00"),
     call("create_event", title="Quarterly review", date="2026-10-08", start_time="15:00")))
add("actx-single-03", "single-tool", "easy",
    single(KO_HEAD, ["get_leave_balance(employee_id: string) — 남은 연차 조회", "submit_leave(employee_id: string, start_date: string, end_date: string) — 연차 신청"],
           ["get_leave_balance(employee_id: string) — remaining annual leave", "submit_leave(employee_id: string, start_date: string, end_date: string) — request leave"]),
    ("사번 E2041 남은 연차 며칠이야?", "How many leave days does employee E2041 have left?"),
    call("get_leave_balance", employee_id="E2041"))
add("actx-single-04", "single-tool", "easy",
    single(KO_HEAD, ["track_parcel(carrier: \"cj\" | \"hanjin\" | \"lotte\" | \"post\", tracking_no: string) — 택배 조회. tracking_no는 하이픈 없이 숫자만 쓴다"],
           ["track_parcel(carrier: \"cj\" | \"hanjin\" | \"lotte\" | \"post\", tracking_no: string) — track a parcel. tracking_no is digits only, no hyphens"]),
    ("CJ대한통운 송장 6012-4471-0938 어디쯤 왔어?", "Where is my CJ Logistics parcel, tracking 6012-4471-0938?"),
    call("track_parcel", carrier="cj", tracking_no="601244710938"))
add("actx-single-05", "single-tool", "easy",
    single(KO_HEAD, ["get_service_status(service_name: string, environment: \"dev\" | \"staging\" | \"prod\") — 서비스 상태 조회", "restart_service(service_name: string, environment: \"dev\" | \"staging\" | \"prod\") — 서비스 재시작"],
           ["get_service_status(service_name: string, environment: \"dev\" | \"staging\" | \"prod\") — check a service", "restart_service(service_name: string, environment: \"dev\" | \"staging\" | \"prod\") — restart a service"]),
    ("스테이징 payment-api 상태 확인해 줘.", "Check the status of payment-api in staging."),
    call("get_service_status", service_name="payment-api", environment="staging"))
add("actx-single-06", "single-tool", "easy",
    single(KO_HEAD, ["get_forecast(city: string, date: string) — 날씨 예보. city는 사용자가 말한 도시 이름 그대로 쓴다"],
           ["get_forecast(city: string, date: string) — weather forecast. city is the city name as the user wrote it"],
           KO_TODAY, EN_TODAY),
    ("내일 부산 날씨 어때?", "What's the weather in Busan tomorrow?"),
    (call("get_forecast", city="부산", date="2026-10-06"), call("get_forecast", city="Busan", date="2026-10-06")))
add("actx-single-07", "single-tool", "easy",
    single(KO_HEAD, ["update_ticket_status(ticket_id: string, status: \"open\" | \"pending\" | \"resolved\" | \"closed\") — 티켓 상태 변경", "get_ticket(ticket_id: string) — 티켓 조회"],
           ["update_ticket_status(ticket_id: string, status: \"open\" | \"pending\" | \"resolved\" | \"closed\") — change a ticket's status", "get_ticket(ticket_id: string) — look up a ticket"]),
    ("티켓 T-5531 해결됨으로 바꿔 줘.", "Mark ticket T-5531 as resolved."),
    call("update_ticket_status", ticket_id="T-5531", status="resolved"))
add("actx-single-08", "single-tool", "easy",
    single(KO_HEAD, ["search_flights(origin: string, destination: string, date: string) — 항공편 검색. 공항은 IATA 3자리 코드로 쓴다"],
           ["search_flights(origin: string, destination: string, date: string) — flight search. Airports are 3-letter IATA codes"],
           KO_TODAY, EN_TODAY),
    ("김포에서 제주 가는 10월 12일 비행기 찾아 줘.", "Find flights from Gimpo to Jeju on October 12."),
    call("search_flights", origin="GMP", destination="CJU", date="2026-10-12"))
add("actx-single-09", "single-tool", "easy",
    single(KO_HEAD, ["add_to_cart(product_id: string, quantity: number) — 장바구니 담기", "remove_from_cart(product_id: string) — 장바구니에서 빼기"],
           ["add_to_cart(product_id: string, quantity: number) — add to cart", "remove_from_cart(product_id: string) — remove from cart"]),
    ("상품 P-88120 세 개 장바구니에 담아 줘.", "Put three of product P-88120 in my cart."),
    call("add_to_cart", product_id="P-88120", quantity=3))
add("actx-single-10", "single-tool", "easy",
    single(KO_HEAD, ["book_appointment(clinic_id: string, date: string, time: string, patient_name: string) — 진료 예약. patient_name은 사용자가 말한 이름 그대로 쓴다"],
           ["book_appointment(clinic_id: string, date: string, time: string, patient_name: string) — book a clinic visit. patient_name is the name exactly as the user gave it"],
           KO_TODAY, EN_TODAY),
    ("강남점(clinic_id GN-02)에 10월 7일 오전 10시 30분으로 예약 잡아 줘. 환자는 이서연.",
     "Book the Gangnam branch (clinic_id GN-02) for October 7 at 10:30 am. The patient is Seoyeon Lee."),
    (call("book_appointment", clinic_id="GN-02", date="2026-10-07", time="10:30", patient_name="이서연"),
     call("book_appointment", clinic_id="GN-02", date="2026-10-07", time="10:30", patient_name="Seoyeon Lee")))

# ---- single tool, complex --------------------------------------------------------------
add("actx-single-11", "date-normalisation", "medium",
    single(KO_HEAD, ["create_event(title: string, date: string, start_time: string) — 일정 등록"],
           ["create_event(title: string, date: string, start_time: string) — add a calendar event"],
           KO_TODAY + " '다음 주'는 오늘이 속한 주의 다음 주(월~일)를 뜻한다.",
           EN_TODAY + " 'Next week' means the Monday-to-Sunday week after the current one."),
    ("다음 주 수요일 아침 9시 반에 '채용 면접' 넣어 줘.", "Add 'Hiring interview' on Wednesday of next week at 9:30 in the morning."),
    (call("create_event", title="채용 면접", date="2026-10-14", start_time="09:30"),
     call("create_event", title="Hiring interview", date="2026-10-14", start_time="09:30")))
add("actx-single-12", "unit-conversion", "medium",
    single(KO_HEAD, ["set_thermostat(zone: \"lobby\" | \"meeting_room\" | \"office\", temperature_c: number) — 온도 설정(섭씨)"],
           ["set_thermostat(zone: \"lobby\" | \"meeting_room\" | \"office\", temperature_c: number) — set temperature in Celsius"]),
    ("회의실 온도 화씨 77도로 맞춰 줘.", "Set the meeting room to 77 degrees Fahrenheit."),
    call("set_thermostat", zone="meeting_room", temperature_c=25))
add("actx-single-13", "enum-argument", "medium",
    single(KO_HEAD, ["update_ticket(ticket_id: string, priority: \"low\" | \"normal\" | \"high\" | \"urgent\") — 티켓 우선순위 변경"],
           ["update_ticket(ticket_id: string, priority: \"low\" | \"normal\" | \"high\" | \"urgent\") — change ticket priority"]),
    ("T-9902 이거 지금 서비스 전체가 멈춘 거라 최우선으로 올려 줘.", "T-9902 is the whole service being down, bump it to the highest priority."),
    call("update_ticket", ticket_id="T-9902", priority="urgent"))
add("actx-single-14", "insufficient-information", "hard",
    single(KO_HEAD, ["transfer(from_account: string, to_account: string, amount_krw: number) — 송금. 세 값이 모두 사용자에게서 명시돼야 하며 추측해서는 안 된다"],
           ["transfer(from_account: string, to_account: string, amount_krw: number) — send money. All three values must be stated by the user; never guess"],
           "필수 값이 없으면 도구를 부르지 말고 {\"tool\": null, \"arguments\": {}} 를 출력해.",
           "If a required value is missing, do not call a tool; output {\"tool\": null, \"arguments\": {}}."),
    ("엄마 계좌로 50만 원 보내 줘.", "Send 500,000 won to my mom's account."),
    {"tool": None}, subset=True)
add("actx-single-15", "no-applicable-tool", "hard",
    single(KO_HEAD, ["get_order(order_id: string) — 주문 조회", "refund_order(order_id: string, reason: string) — 주문 환불"],
           ["get_order(order_id: string) — look up an order", "refund_order(order_id: string, reason: string) — refund an order"]),
    ("이번 달 매출 보고서 PDF로 뽑아 줘.", "Export this month's sales report as a PDF."),
    {"tool": None}, subset=True)
add("actx-single-16", "instruction-revision", "medium",
    single(KO_HEAD, ["restart_service(service_name: string, environment: \"dev\" | \"staging\" | \"prod\") — 서비스 재시작"],
           ["restart_service(service_name: string, environment: \"dev\" | \"staging\" | \"prod\") — restart a service"]),
    ("prod에 auth-service 재시작해. 아 잠깐, prod 말고 staging으로.", "Restart auth-service in prod. Actually wait, not prod, staging."),
    call("restart_service", service_name="auth-service", environment="staging"))
add("actx-single-17", "optional-arguments", "medium",
    single(KO_HEAD, ["search_orders(user_id: string, status?: \"pending\" | \"paid\" | \"shipping\" | \"delivered\" | \"cancelled\", from_date?: string) — 주문 검색. 사용자가 말한 조건만 넣고 말하지 않은 선택 인자는 생략한다"],
           ["search_orders(user_id: string, status?: \"pending\" | \"paid\" | \"shipping\" | \"delivered\" | \"cancelled\", from_date?: string) — search orders. Include only the conditions the user stated; omit unstated optional arguments"]),
    ("회원 U-310 주문 중에 배송 중인 것만 보여 줘.", "Show only the orders of member U-310 that are in shipping."),
    call("search_orders", user_id="U-310", status="shipping"))
add("actx-single-18", "nested-argument", "hard",
    single(KO_HEAD, ["create_invoice(customer_id: string, lines: [{sku: string, qty: number, unit_price_krw: number}]) — 청구서 생성. lines는 사용자가 말한 순서대로 쓴다"],
           ["create_invoice(customer_id: string, lines: [{sku: string, qty: number, unit_price_krw: number}]) — create an invoice. lines follow the order the user mentioned"]),
    ("고객 C-77 청구서 만들어 줘. 모니터(SKU-M1) 2대 대당 32만 원, 케이블(SKU-C3) 5개 개당 8,900원.",
     "Make an invoice for customer C-77: monitors (SKU-M1) x2 at 320,000 won each, cables (SKU-C3) x5 at 8,900 won each."),
    call("create_invoice", customer_id="C-77", lines=[{"sku": "SKU-M1", "qty": 2, "unit_price_krw": 320000}, {"sku": "SKU-C3", "qty": 5, "unit_price_krw": 8900}]))

# ---- multi tool -------------------------------------------------------------------------
add("actx-multi-01", "multi-tool", "hard",
    multi(["book_room(room_id: string, date: string, start_time: string, end_time: string) — 회의실 예약", "send_invite(email: string, event_title: string) — 초대 메일 발송"],
          ["book_room(room_id: string, date: string, start_time: string, end_time: string) — book a room", "send_invite(email: string, event_title: string) — send an invitation"],
          KO_TODAY + " 예약을 먼저 하고 초대를 보낸다.", EN_TODAY + " Book first, then send invitations."),
    ("10월 9일 14:00~15:00에 A3 회의실 잡고, 'API 리뷰'로 jin@corp.kr이랑 mina@corp.kr한테 초대 보내 줘.",
     "Book room A3 on October 9 from 14:00 to 15:00, and invite jin@corp.kr and mina@corp.kr to 'API review'."),
    ({"calls": [call("book_room", room_id="A3", date="2026-10-09", start_time="14:00", end_time="15:00"),
                call("send_invite", email="jin@corp.kr", event_title="API 리뷰"), call("send_invite", email="mina@corp.kr", event_title="API 리뷰")]},
     {"calls": [call("book_room", room_id="A3", date="2026-10-09", start_time="14:00", end_time="15:00"),
                call("send_invite", email="jin@corp.kr", event_title="API review"), call("send_invite", email="mina@corp.kr", event_title="API review")]}))
add("actx-multi-02", "multi-tool", "hard",
    multi(["add_to_cart(product_id: string, quantity: number)", "apply_coupon(code: string)", "checkout(payment_method: \"card\" | \"bank_transfer\" | \"kakaopay\")"],
          ["add_to_cart(product_id: string, quantity: number)", "apply_coupon(code: string)", "checkout(payment_method: \"card\" | \"bank_transfer\" | \"kakaopay\")"],
          "담기 → 쿠폰 → 결제 순서로 실행한다.", "Run add-to-cart, then coupon, then checkout."),
    ("P-1 두 개, P-7 하나 담고 쿠폰 FALL10 적용해서 카카오페이로 결제해 줘.", "Add two P-1 and one P-7, apply coupon FALL10, and pay with KakaoPay."),
    {"calls": [call("add_to_cart", product_id="P-1", quantity=2), call("add_to_cart", product_id="P-7", quantity=1),
               call("apply_coupon", code="FALL10"), call("checkout", payment_method="kakaopay")]})
add("actx-multi-03", "multi-tool", "hard",
    multi(["scale_service(service_name: string, environment: \"dev\" | \"staging\" | \"prod\", replicas: number)", "set_alert(service_name: string, metric: \"cpu\" | \"memory\" | \"latency\", threshold: number) — threshold는 백분율 숫자"],
          ["scale_service(service_name: string, environment: \"dev\" | \"staging\" | \"prod\", replicas: number)", "set_alert(service_name: string, metric: \"cpu\" | \"memory\" | \"latency\", threshold: number) — threshold is a percentage number"]),
    ("prod checkout-api 레플리카 6개로 늘리고, CPU 80% 넘으면 알림 걸어 줘.", "Scale checkout-api in prod to 6 replicas, and alert when CPU goes over 80%."),
    {"calls": [call("scale_service", service_name="checkout-api", environment="prod", replicas=6),
               call("set_alert", service_name="checkout-api", metric="cpu", threshold=80)]})
add("actx-multi-04", "multi-tool", "hard",
    multi(["submit_leave(employee_id: string, start_date: string, end_date: string)", "notify_manager(employee_id: string, message_type: \"leave_request\" | \"overtime\" | \"sick\")"],
          ["submit_leave(employee_id: string, start_date: string, end_date: string)", "notify_manager(employee_id: string, message_type: \"leave_request\" | \"overtime\" | \"sick\")"],
          KO_TODAY + " '이번 주'는 오늘이 속한 월~일이다. 신청 후 알린다.", EN_TODAY + " 'This week' is the current Monday-to-Sunday week. Submit, then notify."),
    ("사번 E1180 이번 주 목요일부터 금요일까지 연차 올리고 팀장한테도 알려 줘.", "File leave for employee E1180 from Thursday to Friday this week and tell their manager."),
    {"calls": [call("submit_leave", employee_id="E1180", start_date="2026-10-08", end_date="2026-10-09"),
               call("notify_manager", employee_id="E1180", message_type="leave_request")]})
add("actx-multi-05", "multi-tool", "hard",
    multi(["transfer(from_account: string, to_account: string, amount_krw: number)"],
          ["transfer(from_account: string, to_account: string, amount_krw: number)"]),
    ("110-111-2222 계좌에서 333-44-55555로 12만 원, 666-77-88888로 3만 5천 원 보내 줘.",
     "From account 110-111-2222, send 120,000 won to 333-44-55555 and 35,000 won to 666-77-88888."),
    {"calls": [call("transfer", from_account="110-111-2222", to_account="333-44-55555", amount_krw=120000),
               call("transfer", from_account="110-111-2222", to_account="666-77-88888", amount_krw=35000)]})
add("actx-multi-06", "multi-tool", "hard",
    multi(["send_sms(phone: string, template: \"shipped\" | \"delayed\") — phone은 사용자가 쓴 형식 그대로"],
          ["send_sms(phone: string, template: \"shipped\" | \"delayed\") — phone exactly as the user wrote it"]),
    ("주문 세 건 중 배송 출발한 건만 문자 보내 줘: 010-1111-2222(출발), 010-3333-4444(지연), 010-5555-6666(출발).",
     "Of these three orders, text only the ones that have shipped: 010-1111-2222 (shipped), 010-3333-4444 (delayed), 010-5555-6666 (shipped)."),
    {"calls": [call("send_sms", phone="010-1111-2222", template="shipped"), call("send_sms", phone="010-5555-6666", template="shipped")]})

# ---- adaptive workflow (next step from observations) ------------------------------------
add("actx-agent-01", "agent-next-step", "hard",
    agent(["find_user(email: string) — 이메일로 user_id 조회", "cancel_subscription(user_id: string, at_period_end: boolean)"],
          ["find_user(email: string) — look up user_id by email", "cancel_subscription(user_id: string, at_period_end: boolean)"],
          "사용자가 당장 해지를 원하면 at_period_end는 false다.", "If the user wants to cancel right away, at_period_end is false."),
    ("kim@acme.co.kr 구독 당장 끊어 줘.\n\n[지금까지 실행]\n1) find_user({\"email\": \"kim@acme.co.kr\"}) → {\"user_id\": \"U-5512\"}",
     "Cancel kim@acme.co.kr's subscription right now.\n\n[Executed so far]\n1) find_user({\"email\": \"kim@acme.co.kr\"}) → {\"user_id\": \"U-5512\"}"),
    call("cancel_subscription", user_id="U-5512", at_period_end=False))
add("actx-agent-02", "agent-next-step", "hard",
    agent(["charge_card(order_id: string, card_id: string)", "list_cards(user_id: string)", "notify_user(user_id: string, template: \"payment_failed\" | \"card_expired\" | \"payment_ok\")"],
          ["charge_card(order_id: string, card_id: string)", "list_cards(user_id: string)", "notify_user(user_id: string, template: \"payment_failed\" | \"card_expired\" | \"payment_ok\")"],
          "결제가 card_expired로 실패하면 사용자의 다른 카드로 재시도한다. 다른 카드가 없으면 notify_user(card_expired)를 보낸다.",
          "If a charge fails with card_expired, retry with another of the user's cards. If there is no other card, send notify_user(card_expired)."),
    ("주문 O-71 결제 처리해 줘. 회원은 U-9.\n\n[지금까지 실행]\n1) charge_card({\"order_id\": \"O-71\", \"card_id\": \"CARD-1\"}) → {\"error\": \"card_expired\"}\n2) list_cards({\"user_id\": \"U-9\"}) → {\"cards\": [\"CARD-1\"]}",
     "Process payment for order O-71. The member is U-9.\n\n[Executed so far]\n1) charge_card({\"order_id\": \"O-71\", \"card_id\": \"CARD-1\"}) → {\"error\": \"card_expired\"}\n2) list_cards({\"user_id\": \"U-9\"}) → {\"cards\": [\"CARD-1\"]}"),
    call("notify_user", user_id="U-9", template="card_expired"))
add("actx-agent-03", "agent-next-step", "hard",
    agent(["search_products(query: string, max_price_krw?: number) — 말하지 않은 선택 인자는 생략"],
          ["search_products(query: string, max_price_krw?: number) — omit optional arguments not needed"],
          "검색 결과가 0건이면 가격 조건을 빼고 같은 검색어로 한 번 더 검색한다.",
          "If a search returns 0 results, search once more with the same query and no price condition."),
    ("3만 원 이하 무선 이어폰 찾아 줘.\n\n[지금까지 실행]\n1) search_products({\"query\": \"무선 이어폰\", \"max_price_krw\": 30000}) → {\"results\": []}",
     "Find wireless earbuds under 30,000 won.\n\n[Executed so far]\n1) search_products({\"query\": \"wireless earbuds\", \"max_price_krw\": 30000}) → {\"results\": []}"),
    (call("search_products", query="무선 이어폰"), call("search_products", query="wireless earbuds")))
add("actx-agent-04", "agent-next-step", "hard",
    agent(["find_customer(name: string)", "get_customer(customer_id: string)"],
          ["find_customer(name: string)", "get_customer(customer_id: string)"],
          "동명이인이 있으면 사용자가 말한 지역과 일치하는 고객을 고른다.",
          "When several customers share a name, pick the one whose city matches what the user said."),
    ("부산 사는 김민준 고객 정보 보여 줘.\n\n[지금까지 실행]\n1) find_customer({\"name\": \"김민준\"}) → {\"matches\": [{\"customer_id\": \"C-11\", \"city\": \"대전\"}, {\"customer_id\": \"C-42\", \"city\": \"부산\"}]}",
     "Show me the customer Kim Minjun who lives in Busan.\n\n[Executed so far]\n1) find_customer({\"name\": \"Kim Minjun\"}) → {\"matches\": [{\"customer_id\": \"C-11\", \"city\": \"Daejeon\"}, {\"customer_id\": \"C-42\", \"city\": \"Busan\"}]}"),
    call("get_customer", customer_id="C-42"))
add("actx-agent-05", "agent-next-step", "hard",
    agent(["retry_job(job_id: string)", "escalate(job_id: string, team: \"sre\" | \"data\")"],
          ["retry_job(job_id: string)", "escalate(job_id: string, team: \"sre\" | \"data\")"],
          "같은 작업은 최대 2번까지만 재시도한다. 2번 재시도해도 실패하면 sre 팀에 escalate한다.",
          "Retry the same job at most twice. If it still fails after two retries, escalate to the sre team."),
    ("배치 작업 J-8 실패했어. 처리해 줘.\n\n[지금까지 실행]\n1) retry_job({\"job_id\": \"J-8\"}) → {\"status\": \"failed\"}\n2) retry_job({\"job_id\": \"J-8\"}) → {\"status\": \"failed\"}",
     "Batch job J-8 failed. Handle it.\n\n[Executed so far]\n1) retry_job({\"job_id\": \"J-8\"}) → {\"status\": \"failed\"}\n2) retry_job({\"job_id\": \"J-8\"}) → {\"status\": \"failed\"}"),
    call("escalate", job_id="J-8", team="sre"))
add("actx-agent-06", "agent-next-step", "hard",
    agent(["update_address(user_id: string, address: string)", "get_user(user_id: string)"],
          ["update_address(user_id: string, address: string)", "get_user(user_id: string)"],
          "변경 후에는 get_user로 확인하고, 확인되면 작업을 끝낸다.",
          "After a change, confirm with get_user; once confirmed, the task is done."),
    ("U-77 주소를 서울 마포구 월드컵북로 396으로 바꿔 줘.\n\n[지금까지 실행]\n1) update_address({\"user_id\": \"U-77\", \"address\": \"서울 마포구 월드컵북로 396\"}) → {\"ok\": true}\n2) get_user({\"user_id\": \"U-77\"}) → {\"address\": \"서울 마포구 월드컵북로 396\"}",
     "Change U-77's address to 396 World Cup buk-ro, Mapo-gu, Seoul.\n\n[Executed so far]\n1) update_address({\"user_id\": \"U-77\", \"address\": \"396 World Cup buk-ro, Mapo-gu, Seoul\"}) → {\"ok\": true}\n2) get_user({\"user_id\": \"U-77\"}) → {\"address\": \"396 World Cup buk-ro, Mapo-gu, Seoul\"}"),
    {"tool": None}, subset=True)

if __name__ == "__main__":
    OUT.write_text(json.dumps(ITEMS, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {len(ITEMS)} items ({len(ITEMS) * 2} tasks) to {OUT.relative_to(ROOT)}")
