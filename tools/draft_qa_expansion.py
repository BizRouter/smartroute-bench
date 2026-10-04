"""Draft the ADR-0149 qa expansion: 20 bilingual items -> tasks/_src/qa-expansion.json.

Grounded and multi-hop items are deterministic (answer_match); the answer is fixed by the material in
the prompt, and arithmetic answers are recomputed below and asserted. Concept items are judge-scored.
Status: DRAFT, not yet cross-reviewed.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tasks/_src/qa-expansion.json"
EN_PATTERN = r"Answer\s*[::]\s*(.+)"
ITEMS = []

KO_NUM = "자료만 근거로 답해. 풀이는 짧게 쓰고, 마지막 줄에 정확히 이 형식으로 답해:\n정답: <숫자만>"
EN_NUM = "Answer only from the material. Keep the reasoning short, then end with exactly this line:\nAnswer: <number only>"
KO_TXT = "자료만 근거로 답해. 풀이는 짧게 쓰고, 마지막 줄에 정확히 이 형식으로 답해:\n정답: {fmt}"
EN_TXT = "Answer only from the material. Keep the reasoning short, then end with exactly this line:\nAnswer: {fmt}"


def det(id_, sub, prior, ko_turn, en_turn, ko_expected, en_expected, numeric=False, block="structured"):
    ko_check = {"type": "answer_match", "expected": ko_expected}
    en_check = {"type": "answer_match", "pattern": EN_PATTERN, "expected": en_expected}
    if numeric:
        ko_check["format"] = en_check["format"] = "numeric"
    ITEMS.append({
        "id": id_, "pair_id": id_, "track": "work", "block": block, "category": "knowledge-qa",
        "subcategory": sub, "difficulty_prior": prior, "mode": "chat", "language": None, "max_tokens": 1024,
        "scoring": "deterministic", "split": "dev", "source": "authored",
        "ko": {"turns": [ko_turn], "check": ko_check}, "en": {"turns": [en_turn], "check": en_check},
    })


def judged(id_, sub, prior, ko_turn, en_turn, ko_rubric, en_rubric):
    ITEMS.append({
        "id": id_, "pair_id": id_, "track": "work", "block": "writing", "category": "knowledge-qa",
        "subcategory": sub, "difficulty_prior": prior, "mode": "chat", "language": None, "max_tokens": 2048,
        "scoring": "judge", "split": "dev", "source": "authored",
        "ko": {"turns": [ko_turn], "rubric": ko_rubric}, "en": {"turns": [en_turn], "rubric": en_rubric},
    })


def rubric(lang, *rows):
    return [{"name": n, "desc": d, "weight": w} for n, d, w in rows]


# ---- grounded answers ---------------------------------------------------------------------
det("qax-grounded-01", "grounded-lookup", "easy",
    "[출장비 규정 제4조] 1박 숙박비 한도: 서울 120,000원, 광역시 100,000원, 그 밖의 지역 80,000원.\n\n질문: 대전 출장 1박 숙박비 한도는 얼마야? (원 단위)\n\n" + KO_NUM,
    "[Travel policy, Art. 4] Nightly lodging cap: Seoul 120,000 won, metropolitan cities 100,000 won, elsewhere 80,000 won. Daejeon is a metropolitan city.\n\nQuestion: What is the nightly lodging cap for a trip to Daejeon, in won?\n\n" + EN_NUM,
    ["100000"], ["100000"], numeric=True)
det("qax-grounded-02", "grounded-lookup", "easy",
    "[환불 정책] 구매 후 7일 이내 요청: 전액 환불. 8~14일: 결제 금액의 50% 환불. 15일 이후: 환불 불가. (구매일을 1일째로 센다)\n\n질문: 구매 10일째에 환불을 요청하면 결제 금액의 몇 %를 돌려받아?\n\n" + KO_NUM,
    "[Refund policy] Requests within 7 days of purchase: full refund. Days 8-14: 50% of the amount paid. From day 15: no refund. (The purchase day counts as day 1.)\n\nQuestion: A refund is requested on day 10. What percentage of the amount paid comes back?\n\n" + EN_NUM,
    ["50"], ["50"], numeric=True)

# Parking: first 30 min free, then 1,000 won per started 10 min, daily cap 20,000 won.
# A receipt of 30,000 won or more gives 2 hours free instead of (not on top of) the first 30 minutes.
parked, purchase = 190, 40000
free = 120 if purchase >= 30000 else 30
units = -(-max(parked - free, 0) // 10)
parking_fee = min(units * 1000, 20000)
assert parking_fee == 7000
det("qax-grounded-03", "grounded-rules", "hard",
    "[주차 요금 안내]\n- 최초 30분 무료, 이후 10분마다 1,000원(10분 미만도 10분으로 계산)\n- 하루 최대 20,000원\n- 당일 영수증 합계 30,000원 이상이면 2시간 무료(최초 30분 무료와 중복 적용하지 않음)\n\n질문: 3시간 10분 주차했고 영수증 합계가 40,000원이야. 주차 요금은 얼마야? (원 단위)\n\n" + KO_NUM,
    "[Parking fees]\n- First 30 minutes free, then 1,000 won per 10 minutes (a partial 10 minutes counts as 10)\n- Daily maximum 20,000 won\n- Same-day receipts totalling 30,000 won or more give 2 hours free (not combined with the first 30 free minutes)\n\nQuestion: I parked for 3 hours 10 minutes and my receipts total 40,000 won. What is the parking fee in won?\n\n" + EN_NUM,
    [str(parking_fee)], [str(parking_fee)], numeric=True)
det("qax-grounded-04", "grounded-lookup", "easy",
    "[점검 공지] 10월 18일(토) 02:00~06:00 전체 시스템 점검. 단, 결제 서비스는 03:00~04:00에만 중단되고 나머지 시간에는 정상 운영합니다.\n\n질문: 결제 서비스가 중단되는 시간은 모두 몇 시간이야?\n\n" + KO_NUM,
    "[Maintenance notice] Full system maintenance on Sat, Oct 18, 02:00-06:00. The payment service, however, is down only 03:00-04:00 and runs normally the rest of the time.\n\nQuestion: For how many hours in total is the payment service down?\n\n" + EN_NUM,
    ["1"], ["1"], numeric=True)
det("qax-grounded-05", "grounded-rules", "hard",
    "[9/30 회의록] 신제품 출시일은 11월 3일로 확정.\n[10/2 정정 공지] 일반 소비자 대상 출시는 11월 10일로 연기한다. B2B 고객 대상 출시는 기존대로 11월 3일에 진행한다.\n\n질문: 일반 소비자 대상 출시일은 언제야?\n\n" + KO_TXT.format(fmt="MM-DD"),
    "[Minutes, 9/30] Launch date fixed for November 3.\n[Correction, 10/2] The consumer launch is postponed to November 10. The B2B launch stays on November 3.\n\nQuestion: When is the consumer launch?\n\n" + EN_TXT.format(fmt="MM-DD"),
    ["11-10", "11월 10일"], ["11-10", "November 10"])
det("qax-grounded-06", "grounded-lookup", "easy",
    "[지점 영업시간]\n| 지점 | 평일 | 토요일 | 일요일 |\n| 강남 | 09:00-21:00 | 10:00-20:00 | 휴무 |\n| 판교 | 09:00-19:00 | 10:00-18:00 | 휴무 |\n| 부산 | 09:30-20:00 | 10:00-17:00 | 11:00-16:00 |\n\n질문: 판교점은 토요일 몇 시에 문을 닫아?\n\n" + KO_TXT.format(fmt="HH:MM"),
    "[Branch hours]\n| Branch | Weekdays | Saturday | Sunday |\n| Gangnam | 09:00-21:00 | 10:00-20:00 | closed |\n| Pangyo | 09:00-19:00 | 10:00-18:00 | closed |\n| Busan | 09:30-20:00 | 10:00-17:00 | 11:00-16:00 |\n\nQuestion: What time does the Pangyo branch close on Saturday?\n\n" + EN_TXT.format(fmt="HH:MM"),
    ["18:00"], ["18:00"])
service_years = 6
leave_days = min(15 + ((service_years - 1) // 2 if service_years >= 3 else 0), 25)
assert leave_days == 17
det("qax-grounded-07", "grounded-rules", "hard",
    "[연차 규정]\n- 근속 1년 이상: 15일\n- 근속 3년 이상: 근속 1년을 넘는 매 2년마다 1일을 더한다(최대 25일)\n\n질문: 만 6년 근속한 직원의 연차는 며칠이야?\n\n" + KO_NUM,
    "[Annual leave rules]\n- 1 year of service or more: 15 days\n- 3 years of service or more: add 1 day for every 2 years of service beyond the first year (maximum 25 days)\n\nQuestion: How many days of annual leave does an employee with exactly 6 full years of service get?\n\n" + EN_NUM,
    [str(leave_days)], [str(leave_days)], numeric=True)
det("qax-grounded-08", "grounded-rules", "hard",
    "[배송비 안내]\n- 기본 배송비 3,000원\n- 주문 금액 30,000원 이상 무료배송\n- 제주·도서산간 지역은 추가 배송비 3,000원(무료배송 주문에도 부과)\n\n질문: 제주에 사는 고객이 35,000원어치 주문했어. 배송비는 얼마야? (원 단위)\n\n" + KO_NUM,
    "[Shipping fees]\n- Standard shipping 3,000 won\n- Free shipping on orders of 30,000 won or more\n- Jeju and remote islands: an extra 3,000 won (charged even on free-shipping orders)\n\nQuestion: A customer in Jeju orders 35,000 won of goods. What is the shipping fee in won?\n\n" + EN_NUM,
    ["3000"], ["3000"], numeric=True)

# ---- multi-hop over stated facts ------------------------------------------------------------
det("qax-multihop-01", "multihop", "medium",
    "[조직 정보] A는 B의 직속 상사다. B는 C의 직속 상사다. C는 D의 멘토다. 휴가 결재는 직속 상사만 할 수 있다.\n\n질문: C의 휴가는 누가 결재해?\n\n" + KO_TXT.format(fmt="<이름 한 글자>"),
    "[Org facts] A is B's direct manager. B is C's direct manager. C is D's mentor. Only a direct manager can approve leave.\n\nQuestion: Who approves C's leave?\n\n" + EN_TXT.format(fmt="<one letter>"),
    ["B"], ["B"])
floor_dev = 3
floor_design = floor_dev + 1
floor_sales = floor_design - 2
assert floor_sales == 2
det("qax-multihop-02", "multihop", "hard",
    "[층 배치] 개발팀은 3층이다. 디자인팀은 개발팀 바로 위층이다. 영업팀은 디자인팀보다 두 층 아래다. 대회의실은 영업팀과 같은 층이다.\n\n질문: 대회의실은 몇 층이야?\n\n" + KO_NUM,
    "[Floor plan] The engineering team is on floor 3. The design team is on the floor directly above engineering. The sales team is two floors below design. The large meeting room is on the same floor as sales.\n\nQuestion: Which floor is the large meeting room on?\n\n" + EN_NUM,
    [str(floor_sales)], [str(floor_sales)], numeric=True)
det("qax-multihop-03", "multihop", "hard",
    "[일정] 프로젝트 Y는 10월 6일에 시작해서 5일 동안 진행한다(시작일 포함). 프로젝트 X는 Y가 끝난 다음 날 시작한다. X의 리뷰는 X 시작일을 1일째로 셀 때 3일째에 한다.\n\n질문: X의 리뷰 날짜는?\n\n" + KO_TXT.format(fmt="MM-DD"),
    "[Schedule] Project Y starts on October 6 and runs for 5 days (start day included). Project X starts the day after Y ends. X's review is on day 3 of X, counting X's start day as day 1.\n\nQuestion: What is the date of X's review?\n\n" + EN_TXT.format(fmt="MM-DD"),
    ["10-13", "10월 13일"], ["10-13", "October 13"])
capacity_p = 4000
capacity_q = capacity_p * 125 // 100
capacity_r = capacity_q - 500
assert capacity_r == 4500
det("qax-multihop-04", "multihop", "medium",
    "[제품 사양] 모델 P의 배터리는 4,000mAh다. 모델 Q의 배터리는 P보다 25% 크다. 모델 R의 배터리는 Q보다 500mAh 작다.\n\n질문: 모델 R의 배터리 용량은 몇 mAh야?\n\n" + KO_NUM,
    "[Specs] Model P has a 4,000 mAh battery. Model Q's battery is 25% larger than P's. Model R's battery is 500 mAh smaller than Q's.\n\nQuestion: What is model R's battery capacity in mAh?\n\n" + EN_NUM,
    [str(capacity_r)], [str(capacity_r)], numeric=True)
det("qax-multihop-05", "multihop", "medium",
    "[법인카드 규정] 서울 지사 직원의 법인카드 한도는 월 2,000,000원이다. 팀장은 소속 지사 한도의 1.5배를 쓴다.\n[인사 정보] 민지는 서울 지사 소속 팀장이다.\n\n질문: 민지의 월 법인카드 한도는 얼마야? (원 단위)\n\n" + KO_NUM,
    "[Corporate card policy] Seoul office staff have a monthly corporate card limit of 2,000,000 won. Team leads get 1.5 times their office's limit.\n[HR record] Minji is a team lead in the Seoul office.\n\nQuestion: What is Minji's monthly card limit in won?\n\n" + EN_NUM,
    ["3000000"], ["3000000"], numeric=True)
det("qax-multihop-06", "multihop", "hard",
    "[회의 정보] 회의는 서울 시각 오전 9:00에 시작한다. 서울은 UTC+9이고, 런던은 지금 서머타임이라 UTC+1이다.\n\n질문: 런던 참석자에게 회의 시작은 현지 시각으로 몇 시야? (24시간제)\n\n" + KO_TXT.format(fmt="HH:MM"),
    "[Meeting] The meeting starts at 09:00 Seoul time. Seoul is UTC+9; London is on summer time, so UTC+1.\n\nQuestion: What local time does the meeting start for the London attendee? (24-hour clock)\n\n" + EN_TXT.format(fmt="HH:MM"),
    ["01:00"], ["01:00"])

# ---- fact recall ------------------------------------------------------------------------------
det("qax-fact-01", "fact-recall", "easy",
    "대한민국 헌법에 따른 대통령의 임기는 몇 년이야? 마지막 줄에 정확히 이 형식으로 답해:\n정답: <숫자만>",
    "Under the Constitution of the Republic of Korea, how many years is the president's term? End with exactly this line:\nAnswer: <number only>",
    ["5"], ["5"], numeric=True, block="judgment")
det("qax-fact-02", "fact-recall", "easy",
    "국제단위계(SI)에서 전류의 기본 단위 이름은 뭐야? 마지막 줄에 정확히 이 형식으로 답해:\n정답: <단위 이름>",
    "In the International System of Units (SI), what is the name of the base unit of electric current? End with exactly this line:\nAnswer: <unit name>",
    ["암페어", "ampere", "A"], ["ampere", "amperes", "A"], block="judgment")
det("qax-fact-03", "fact-recall", "easy",
    "HTTP 상태 코드 404의 표준 이름(영문 reason phrase)은 뭐야? 마지막 줄에 정확히 이 형식으로 답해:\n정답: <영문 이름>",
    "What is the standard reason phrase of HTTP status code 404? End with exactly this line:\nAnswer: <reason phrase>",
    ["Not Found"], ["Not Found"], block="judgment")

# ---- concept explanation (judge) ---------------------------------------------------------------
judged("qax-concept-01", "concept-explanation", "medium",
       "데이터베이스 트랜잭션 격리 수준 중 'Repeatable Read'가 막아 주는 현상과 막지 못할 수 있는 현상을, 짧은 예시와 함께 설명해 줘.",
       "Explain which anomalies the 'Repeatable Read' transaction isolation level prevents and which it may not prevent, with a short example.",
       rubric("ko", ("정확성", "dirty read·non-repeatable read 방지와 phantom read 가능성을 정확히 설명하고, DBMS별 구현 차이(예: MVCC)를 틀리게 말하지 않았는가", 0.5),
              ("예시", "예시가 현상을 구체적으로 보여 주는가", 0.3), ("명료성", "군더더기 없이 구조적으로 설명했는가", 0.2)),
       rubric("en", ("Accuracy", "Correctly states that dirty and non-repeatable reads are prevented and phantoms may occur, without misstating DBMS differences (e.g. MVCC)", 0.5),
              ("Example", "The example concretely shows the anomaly", 0.3), ("Clarity", "Structured and free of padding", 0.2)))
judged("qax-concept-02", "concept-explanation", "medium",
       "부가가치세의 '매입세액 공제'가 무엇이고 왜 이런 제도가 있는지, 소규모 사업자 입장에서 예를 들어 설명해 줘.",
       "Explain what input VAT credit (deducting VAT paid on purchases) is and why the system has it, with an example from a small business's point of view.",
       rubric("ko", ("정확성", "매출세액에서 매입세액을 빼서 납부한다는 구조와 이중과세 방지 취지를 정확히 설명했는가", 0.5),
              ("예시", "금액이 있는 예시로 계산 구조를 보여 주는가", 0.3), ("명료성", "사업자가 이해하기 쉬운 표현인가", 0.2)),
       rubric("en", ("Accuracy", "Correctly explains paying output VAT minus input VAT and the aim of avoiding cascading tax", 0.5),
              ("Example", "Shows the computation with a numeric example", 0.3), ("Clarity", "Easy for a business owner to follow", 0.2)))
judged("qax-concept-03", "concept-explanation", "easy",
       "머신러닝에서 정밀도(precision)와 재현율(recall)의 차이를 스팸 메일 필터 예로 설명하고, 어떤 상황에서 어느 쪽을 더 중시하는지 말해 줘.",
       "Explain the difference between precision and recall using a spam filter, and say when you would favour each.",
       rubric("ko", ("정확성", "두 지표의 정의(오탐·미탐 관점)를 정확히 설명했는가", 0.5),
              ("적용", "스팸 예시와 우선순위 판단이 타당한가", 0.3), ("명료성", "간결하고 이해하기 쉬운가", 0.2)),
       rubric("en", ("Accuracy", "Defines both metrics correctly in terms of false positives and false negatives", 0.5),
              ("Application", "The spam example and the trade-off judgement are sound", 0.3), ("Clarity", "Concise and easy to follow", 0.2)))

if __name__ == "__main__":
    OUT.write_text(json.dumps(ITEMS, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {len(ITEMS)} items ({len(ITEMS) * 2} tasks) to {OUT.relative_to(ROOT)}")
