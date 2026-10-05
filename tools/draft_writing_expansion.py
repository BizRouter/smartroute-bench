"""Draft the ADR-0149 create / dialogue / transform expansions (32 bilingual items).

Writes tasks/_src/create-expansion.json, dialogue-expansion.json and transform-expansion.json.
Classification and conversion items are deterministic (json_match); writing items are judged, with a
mechanical constraints check where a rule is objective (length, line count, required facts).
Status: DRAFT, not yet cross-reviewed.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tasks/_src"
OUT = {"create": [], "dialogue": [], "transform": []}


def rubric(*rows):
    return [{"name": n, "desc": d, "weight": w} for n, d, w in rows]


def item(group, id_, category, sub, prior, block, scoring, ko, en, max_tokens=2048, system=None):
    rec = {"id": id_, "pair_id": id_, "track": "work", "block": block, "category": category, "subcategory": sub,
           "difficulty_prior": prior, "mode": "chat", "language": None, "max_tokens": max_tokens,
           "scoring": scoring, "split": "dev", "source": "authored", "ko": ko, "en": en}
    OUT[group].append(rec)


def side(turns, rub=None, check=None, system=None):
    s = {"turns": turns if isinstance(turns, list) else [turns]}
    if system:
        s["system"] = system
    if rub:
        s["rubric"] = rub
    if check:
        s["check"] = check
    return s


def constraints(*rules):
    return {"type": "constraints", "rules": list(rules)}


W_KO = ("완성도", "요청한 구성 요소를 빠짐없이 갖췄는가"), ("품질", "목적과 독자에 맞게 정확하고 설득력 있는가"), ("자연스러움", "번역투 없는 자연스러운 한국어인가")
W_EN = ("Completeness", "Includes every requested element"), ("Quality", "Accurate and effective for the purpose and reader"), ("Fluency", "Natural, idiomatic English")


def std_rubric(lang, weights=(0.35, 0.45, 0.2)):
    rows = W_KO if lang == "ko" else W_EN
    return rubric(*[(n, d, w) for (n, d), w in zip(rows, weights)])


# ==== create ===========================================================================================
item("create", "crx-biz-01", "business-writing", "apology-notice", "hard", "writing", "both",
     side("어제 14:00~16:30 결제 장애로 주문이 실패한 고객에게 보낼 사과 안내문을 써 줘. 원인은 결제대행사 연동 서버 장애였고 지금은 복구됐어. 보상으로 10,000원 쿠폰을 지급하고, 쿠폰 유효기간은 11월 30일까지야. 600자 이내로 써.",
          rubric(("사실 전달", "장애 시간·원인·복구 사실·보상 조건을 정확히 담았는가", 0.4), ("어조", "책임을 회피하지 않고 진정성 있는 사과인가", 0.35), ("명료성", "고객이 할 일(쿠폰 사용)이 분명한가", 0.25)),
          constraints({"kind": "max_chars", "n": 600}, {"kind": "require", "values": ["10,000원", "11월 30일"]})),
     side("Write an apology notice to customers whose orders failed during yesterday's payment outage from 14:00 to 16:30. The cause was an outage at the payment gateway's integration server, and service is restored. As compensation each customer gets a 10,000 won coupon, valid until November 30. Keep it under 1,200 characters.",
          rubric(("Facts", "States the outage window, cause, restoration and coupon terms accurately", 0.4), ("Tone", "A sincere apology that does not deflect responsibility", 0.35), ("Clarity", "Makes clear what the customer should do (use the coupon)", 0.25)),
          constraints({"kind": "max_chars", "n": 1200}, {"kind": "require", "values": ["10,000 won", "November 30"]})))
item("create", "crx-biz-02", "business-writing", "price-negotiation", "hard", "writing", "both",
     side("거래처(주식회사 한빛상사) 구매팀장에게 보낼 단가 인상 요청 메일을 써 줘. 내년 1월 1일부터 단가 6% 인상. 근거는 원자재가 상승, 물류비 상승, 최저임금 인상 세 가지. 거래 관계를 해치지 않도록 정중하게, 협의 일정 제안도 넣어 줘.",
          rubric(("요구 충족", "인상률·적용일·근거 3가지·협의 제안을 모두 담았는가", 0.35), ("설득력", "근거가 구체적이고 상대 입장을 배려하는가", 0.4), ("격식", "업무 메일 형식과 존댓말이 적절한가", 0.25)),
          constraints({"kind": "require", "values": ["6%", "1월 1일"]})),
     side("Write an email to the purchasing manager of our client, Hanbit Trading Co., requesting a 6% unit-price increase effective January 1 next year. The reasons are rising raw-material costs, higher logistics costs and the minimum-wage increase. Keep it courteous so the relationship is not harmed, and propose a time to discuss.",
          rubric(("Requirements", "Includes the rate, effective date, all three reasons and a meeting proposal", 0.35), ("Persuasiveness", "Specific reasons with consideration for the client", 0.4), ("Register", "Appropriate business-email format and tone", 0.25)),
          constraints({"kind": "require", "values": ["6%", "January 1"]})))
item("create", "crx-tech-01", "technical-writing", "api-spec", "hard", "writing", "both",
     side("환불 API 명세를 마크다운으로 써 줘. 엔드포인트는 POST /v1/refunds. 요청 필드: order_id(필수), amount_krw(선택, 없으면 전액), reason(필수). 같은 요청이 두 번 와도 한 번만 처리되도록 Idempotency-Key 헤더를 필수로 받아. 응답: 201 생성, 400 필드 오류, 404 주문 없음, 409 이미 환불됨. 요청·응답 예시를 하나씩 넣어 줘.",
          rubric(("정확성", "필드 필수 여부·상태 코드·멱등성 규칙이 요구와 일치하는가", 0.45), ("완성도", "요청/응답 예시와 오류 설명이 실제로 쓸 만한가", 0.35), ("구조", "개발자가 찾기 쉬운 구조인가", 0.2)),
          constraints({"kind": "require", "values": ["POST /v1/refunds", "Idempotency-Key", "201", "409"]})),
     side("Write a refund API spec in Markdown. Endpoint: POST /v1/refunds. Request fields: order_id (required), amount_krw (optional; full refund when absent), reason (required). Require an Idempotency-Key header so a repeated request is processed only once. Responses: 201 created, 400 field error, 404 order not found, 409 already refunded. Include one request and one response example.",
          rubric(("Accuracy", "Field requirements, status codes and idempotency rule match the brief", 0.45), ("Completeness", "Examples and error descriptions are usable", 0.35), ("Structure", "Easy for developers to scan", 0.2)),
          constraints({"kind": "require", "values": ["POST /v1/refunds", "Idempotency-Key", "201", "409"]})))
item("create", "crx-tech-02", "technical-writing", "runbook", "medium", "writing", "judge",
     side("서버 디스크 사용률 90% 경보가 왔을 때 당직자가 따라 할 대응 런북을 써 줘. 확인 → 원인 파악 → 조치 → 에스컬레이션 기준 순서로, 실행할 명령 예시를 넣고, 하지 말아야 할 일(예: 로그 디렉터리 통째 삭제)도 적어 줘.", std_rubric("ko")),
     side("Write an on-call runbook for a server disk-usage alert at 90%. Order it as check → find the cause → act → escalation criteria, include example commands, and list things not to do (such as deleting a whole log directory).", std_rubric("en")))
item("create", "crx-tech-03", "technical-writing", "onboarding-guide", "easy", "writing", "judge",
     side("Git을 처음 쓰는 신입 개발자를 위한 사내 위키 글을 써 줘. 우리 팀은 main 보호, feature 브랜치, PR 리뷰 1명 이상 승인, squash merge를 쓴다. 하루 작업 흐름을 순서대로 보여 줘.", std_rubric("ko")),
     side("Write an internal wiki page for a new developer who has never used Git. Our team protects main, works on feature branches, needs at least one PR approval, and squash-merges. Show a day's workflow step by step.", std_rubric("en")))
item("create", "crx-tech-04", "technical-writing", "readme", "medium", "writing", "judge",
     side("사내 CLI 도구 'bz-sync'의 README 설치·사용 절을 써 줘. macOS(Homebrew)와 Linux(.deb) 설치, 최초 로그인(bz-sync login), 폴더 동기화(bz-sync push <폴더>), 자주 나는 오류 2개(권한 거부, 토큰 만료)와 해결법을 넣어.", std_rubric("ko")),
     side("Write the install and usage sections of the README for an internal CLI tool, 'bz-sync'. Cover macOS (Homebrew) and Linux (.deb) installation, first login (bz-sync login), syncing a folder (bz-sync push <folder>), and two common errors (permission denied, expired token) with fixes.", std_rubric("en")))
item("create", "crx-design-01", "design", "db-schema", "hard", "writing", "judge",
     side("병원 진료 예약 시스템의 DB 스키마를 설계해 줘. 요구: 의사는 여러 진료과에 속할 수 있다, 예약은 30분 단위 슬롯, 같은 의사의 같은 슬롯은 중복 예약 불가, 환자는 예약을 취소할 수 있고 취소 이력이 남아야 한다. 테이블·주요 컬럼·키·제약 조건을 정리하고 설계 이유를 짧게 써 줘.",
          rubric(("정확성", "다대다 관계·슬롯 중복 방지 제약·취소 이력 요구를 정확히 반영했는가", 0.5), ("설계 품질", "정규화·키·인덱스 선택이 합리적인가", 0.3), ("설명", "설계 이유가 분명한가", 0.2))),
     side("Design the database schema for a clinic appointment system. Requirements: a doctor can belong to several departments; appointments use 30-minute slots; the same doctor's slot cannot be double-booked; patients can cancel, and cancellation history must be kept. List tables, key columns, keys and constraints, with brief reasons.",
          rubric(("Correctness", "Captures the many-to-many relation, the no-double-booking constraint and cancellation history", 0.5), ("Design quality", "Sound normalization, keys and indexes", 0.3), ("Explanation", "Clear reasons", 0.2))))
item("create", "crx-design-02", "design", "api-design", "medium", "writing", "judge",
     side("쇼핑몰 장바구니 기능의 REST API를 설계해 줘. 담기, 수량 변경, 빼기, 목록 조회, 비로그인 장바구니를 로그인 시 합치기가 필요해. 엔드포인트·메서드·요청/응답 핵심 필드·오류 상황을 표로 정리해 줘.", std_rubric("ko")),
     side("Design a REST API for an online store's cart: add, change quantity, remove, list, and merge a guest cart on login. Tabulate endpoints, methods, key request/response fields and error cases.", std_rubric("en")))
item("create", "crx-design-03", "design", "information-architecture", "medium", "writing", "judge",
     side("동네 병원 모바일 앱의 정보 구조(IA)를 설계해 줘. 주요 기능: 예약, 진료 대기 순번, 처방전 보기, 병원 공지, 마이페이지. 하단 탭 구성과 각 탭의 하위 화면을 계층으로 보여 주고, 노년층 사용자를 고려한 설계 원칙 3가지를 덧붙여.", std_rubric("ko")),
     side("Design the information architecture of a neighborhood clinic's mobile app. Main features: booking, queue number, prescriptions, clinic notices, my page. Show the bottom-tab layout and each tab's sub-screens as a hierarchy, plus three design principles for older users.", std_rubric("en")))
item("create", "crx-creative-01", "creative-writing", "short-story", "easy", "writing", "both",
     side("고양이가 주인공인 짧은 동화를 써 줘. 다섯 살 아이에게 읽어 줄 거고, '나눔'에 대한 교훈이 자연스럽게 들어가야 해. 450자 이내.",
          rubric(("창의성", "아이가 흥미를 느낄 만한 이야기인가", 0.4), ("교훈", "교훈이 설교조 없이 자연스러운가", 0.35), ("눈높이", "다섯 살에게 맞는 어휘·문장인가", 0.25)),
          constraints({"kind": "max_chars", "n": 450})),
     side("Write a short fairy tale with a cat as the hero, to read to a five-year-old, with a natural lesson about sharing. Under 900 characters.",
          rubric(("Creativity", "A story a child would enjoy", 0.4), ("Lesson", "The lesson lands without preaching", 0.35), ("Level", "Vocabulary and sentences fit a five-year-old", 0.25)),
          constraints({"kind": "max_chars", "n": 900})))
item("create", "crx-creative-02", "creative-writing", "slogan", "easy", "writing", "both",
     side("수제 그래놀라 브랜드 '아침숲'의 슬로건을 5개 써 줘. 한 줄에 하나씩, 번호나 설명 없이 슬로건만 5줄로.",
          rubric(("독창성", "진부하지 않고 기억에 남는가", 0.5), ("브랜드 적합성", "수제·건강·아침 이미지와 맞는가", 0.3), ("형식", "짧고 리듬감 있는가", 0.2)),
          constraints({"kind": "line_count", "n": 5})),
     side("Write 5 slogans for 'Morning Grove', a handmade granola brand. One per line, slogans only, no numbers or explanations, exactly 5 lines.",
          rubric(("Originality", "Fresh and memorable", 0.5), ("Brand fit", "Fits handmade, healthy, morning", 0.3), ("Form", "Short and rhythmic", 0.2)),
          constraints({"kind": "line_count", "n": 5})))
item("create", "crx-creative-03", "creative-writing", "poem", "medium", "writing", "both",
     side("늦가을 퇴근길을 소재로 시를 써 줘. 3연, 각 연 4행. 제목은 쓰지 말고, 연 사이에는 빈 줄 하나만 둬.",
          rubric(("시적 표현", "이미지와 정서가 살아 있는가", 0.5), ("구성", "연 사이 흐름이 있는가", 0.3), ("형식 준수", "3연 4행을 지켰는가", 0.2)),
          constraints({"kind": "line_count", "n": 12})),
     side("Write a poem about the commute home in late autumn. Three stanzas of four lines each. No title; separate stanzas with a single blank line.",
          rubric(("Poetic expression", "Vivid images and feeling", 0.5), ("Structure", "The stanzas flow", 0.3), ("Form", "Keeps 3 stanzas of 4 lines", 0.2)),
          constraints({"kind": "line_count", "n": 12})))

# ==== dialogue (multi-turn) ============================================================================
DLG_KO = rubric(("맥락 적응", "앞 턴의 정보와 사용자의 새 조건을 반영해 대화를 이어 가는가", 0.4), ("유용성", "구체적이고 실제로 도움이 되는가", 0.35), ("대화성", "일방적인 장문 대신 자연스럽게 주고받는가", 0.25))
DLG_EN = rubric(("Context", "Builds on earlier turns and the user's new conditions", 0.4), ("Usefulness", "Concrete and genuinely helpful", 0.35), ("Conversational", "Natural back-and-forth rather than a monologue", 0.25))
SOC_KO = rubric(("공감", "상대의 감정에 맞게 반응하는가", 0.45), ("자연스러움", "사람처럼 편안하고 과하지 않은가", 0.35), ("연결", "대화를 이어 갈 계기를 주는가", 0.2))
SOC_EN = rubric(("Empathy", "Responds to the person's feelings", 0.45), ("Naturalness", "Relaxed and not overdone", 0.35), ("Connection", "Gives the conversation somewhere to go", 0.2))


def dialogue(id_, sub, prior, ko_turns, en_turns, social=False):
    item("dialogue", id_, "dialogue", sub, prior, "writing", "judge",
         side(ko_turns, SOC_KO if social else DLG_KO), side(en_turns, SOC_EN if social else DLG_EN))


dialogue("dlx-brainstorm-01", "brainstorm", "medium",
         ["팀 워크숍 아이디어 좀 같이 생각해 줘. 12명이고 반나절이야.", "좋네. 근데 몸 쓰는 건 싫다는 사람이 좀 있어. 그리고 예산은 1인 3만 원.", "그중 두 개만 골라서 시간표로 짜 줄래?"],
         ["Help me brainstorm a team workshop. 12 people, half a day.", "Nice. Some people don't want anything physical, though. And the budget is 30,000 won per person.", "Pick just two of those and lay them out as a schedule?"])
dialogue("dlx-brainstorm-02", "brainstorm", "medium",
         ["동네 카페 겨울 신메뉴 아이디어 내 줘.", "비건 손님이 늘었어. 비건 메뉴로 다시 생각해 줄래?", "원가는 잔당 1,500원 안쪽이어야 해. 가능한 것만 남겨 줘."],
         ["Give me winter menu ideas for a neighborhood café.", "We're getting more vegan customers. Can you rethink it as vegan options?", "Cost has to stay under 1,500 won a cup. Keep only the ones that work."])
dialogue("dlx-brainstorm-03", "brainstorm", "easy",
         ["세무사인데 유튜브를 시작하려고 해. 어떤 콘텐츠가 좋을까?", "직장인 대상이 더 좋을 것 같아. 첫 달에 올릴 영상 4개 제목만 뽑아 줘."],
         ["I'm a tax accountant starting a YouTube channel. What content would work?", "I think targeting office workers is better. Give me just the titles of four videos for the first month."])
dialogue("dlx-brainstorm-04", "brainstorm", "medium",
         ["사내 해커톤 주제를 정해야 해. 아이디어 좀 줘.", "우리 회사는 물류 회사야. 그리고 이틀 안에 데모가 나와야 해.", "제일 현실적인 걸 하나 골라서 이유를 말해 줘."],
         ["We need a theme for our internal hackathon. Any ideas?", "We're a logistics company, and teams must have a demo in two days.", "Pick the most realistic one and tell me why."])
dialogue("dlx-guide-01", "guide", "medium",
         ["엑셀에서 다른 시트의 단가를 상품코드로 찾아오고 싶어. 어떻게 해?", "알려 준 대로 했는데 #N/A가 떠.", "상품코드가 한쪽은 숫자고 한쪽은 텍스트인 것 같아. 어떻게 맞춰?"],
         ["In Excel I want to pull the unit price from another sheet by product code. How?", "I did what you said but I get #N/A.", "I think the codes are numbers on one side and text on the other. How do I make them match?"])
dialogue("dlx-guide-02", "guide", "easy",
         ["새 공유기 와이파이 이름이랑 비밀번호를 바꾸고 싶어.", "192.168.0.1 들어가 봤는데 페이지가 안 열려.", "공유기 뒤에 192.168.1.1이라고 적혀 있네."],
         ["I want to change the Wi-Fi name and password on my new router.", "I tried 192.168.0.1 but the page won't open.", "The label on the back of the router says 192.168.1.1."])
dialogue("dlx-guide-03", "guide", "medium",
         ["연말정산 간소화 서비스 처음 써 보는데 뭐부터 하면 돼?", "부양가족 의료비가 안 보여. 엄마 병원비를 내가 냈거든.", "엄마가 자료 제공 동의를 해야 한다는 거지? 엄마는 스마트폰을 잘 못 써."],
         ["I'm using the year-end tax settlement simplified service for the first time. Where do I start?", "My dependent's medical expenses don't show up. I paid my mom's hospital bills.", "So my mom has to consent to share her records? She isn't good with smartphones."])
dialogue("dlx-guide-04", "guide", "medium",
         ["윈도우에 파이썬 처음 설치하려고 해. 순서대로 알려 줘.", "설치는 됐는데 명령 프롬프트에 python 치면 마이크로소프트 스토어가 열려.", "그 설정을 껐는데 이제는 'python'은 내부 또는 외부 명령이 아니래."],
         ["I'm installing Python on Windows for the first time. Walk me through it.", "It installed, but typing python in Command Prompt opens the Microsoft Store.", "I turned that setting off, and now it says 'python' is not recognized as a command."])
dialogue("dlx-guide-05", "guide", "medium",
         ["주 3회 집에서 할 근력 운동 루틴 짜 줘. 초보야.", "아 맞다, 무릎이 안 좋아서 쪼그려 앉는 건 아파.", "기구는 덤벨 한 쌍만 있어. 첫 주 계획으로 정리해 줘."],
         ["Make me a beginner strength routine for three days a week at home.", "Oh, right, my knees are bad, so squatting hurts.", "All I have is one pair of dumbbells. Lay it out as a first-week plan."])
dialogue("dlx-social-01", "social-chat", "easy",
         ["아 오늘 진짜 길었다. 퇴근길 지하철도 꽉 찼어.", "그치. 그래도 내일은 금요일이라 좀 버틸 만해."],
         ["Ugh, today was so long. The subway home was packed too.", "Right. At least tomorrow's Friday, so it's bearable."], social=True)
dialogue("dlx-social-02", "social-chat", "easy",
         ["나 드디어 정보처리기사 합격했어!", "세 번째 도전이었거든. 이번엔 진짜 포기할까 했었어."],
         ["I finally passed the Engineer Information Processing exam!", "It was my third try. I almost gave up this time."], social=True)
dialogue("dlx-social-03", "social-chat", "easy",
         ["우리 강아지가 오늘 처음으로 '손' 했어 ㅎㅎ", "두 달 동안 간식으로 연습했거든. 다음엔 뭘 가르쳐 볼까?"],
         ["My puppy gave me a paw for the first time today, haha.", "We practiced with treats for two months. What should I teach next?"], social=True)

# ==== transform =======================================================================================
def det_json(id_, sub, prior, ko, en, expected_ko, expected_en=None):
    item("transform", id_, "transform", sub, prior, "structured", "deterministic",
         side(ko, check={"type": "json_match", "expected": expected_ko}),
         side(en, check={"type": "json_match", "expected": expected_en if expected_en is not None else expected_ko}), max_tokens=1024)


JSON_KO = "\n\nJSON 객체 하나만 출력해. 설명·코드블록 표시 금지."
JSON_EN = "\n\nOutput exactly one JSON object and nothing else. No prose or code fence."
det_json("trx-classify-01", "classify", "easy",
         "고객 문의를 분류해. 범주는 delivery, refund, payment, other 중 하나다. 결과는 {\"1\": 범주, …} 형식이다.\n1. 택배가 아직 안 왔어요\n2. 카드 결제가 두 번 됐어요\n3. 사이즈가 안 맞아 돈 돌려받고 싶어요\n4. 매장 영업시간이 궁금해요\n5. 송장번호 조회가 안 돼요\n6. 무통장입금 계좌가 어디죠?" + JSON_KO,
         "Classify the customer messages. Categories: delivery, refund, payment, other. Output {\"1\": category, …}.\n1. My parcel hasn't arrived yet\n2. My card was charged twice\n3. The size is wrong and I want my money back\n4. What are your store hours?\n5. I can't look up my tracking number\n6. Which account do I pay to by bank transfer?" + JSON_EN,
         {"1": "delivery", "2": "payment", "3": "refund", "4": "other", "5": "delivery", "6": "payment"})
det_json("trx-classify-02", "classify", "medium",
         "상품 리뷰의 감성을 positive, negative, neutral 중 하나로 분류해. 장점과 단점이 함께 있으면 마지막 문장의 평가를 따른다. 결과는 {\"1\": 감성, …} 형식이다.\n1. 배송 빠르고 포장도 꼼꼼해요.\n2. 색은 예쁜데 한 번 빨았더니 늘어났어요. 다시는 안 살래요.\n3. 사진이랑 같아요.\n4. 가격이 좀 비싸지만 품질 생각하면 만족해요.\n5. 생각보다 작아요." + JSON_KO,
         "Classify each review's sentiment as positive, negative or neutral. When a review has both pros and cons, follow the judgement in its last sentence. Output {\"1\": sentiment, …}.\n1. Fast shipping and careful packaging.\n2. Nice color, but it stretched after one wash. I won't buy again.\n3. Same as the photo.\n4. A bit pricey, but I'm happy given the quality.\n5. Smaller than I expected." + JSON_EN,
         {"1": "positive", "2": "negative", "3": "neutral", "4": "positive", "5": "negative"})
det_json("trx-classify-03", "classify", "hard",
         "장애 티켓에 우선순위를 매겨. 규칙: 결제나 로그인이 안 되면 P1. 그 밖에 고객 다수에게 보이는 기능 오류면 P2. 내부 도구 문제나 일부 고객의 화면 깨짐은 P3. 결과는 {\"T1\": 등급, …} 형식이다.\nT1: 안드로이드 일부 기기에서 배너 이미지가 잘림\nT2: 전체 사용자 로그인 실패\nT3: 상품 검색 결과가 모든 사용자에게 비어 나옴\nT4: 사내 정산 대시보드 느림\nT5: 카카오페이 결제 승인 실패" + JSON_KO,
         "Assign priorities to incident tickets. Rules: payment or login not working is P1. Otherwise, a functional error visible to most customers is P2. Internal tool problems, or display glitches for some customers, are P3. Output {\"T1\": priority, …}.\nT1: Banner image cropped on some Android devices\nT2: Login fails for all users\nT3: Product search returns empty results for every user\nT4: Internal settlement dashboard is slow\nT5: KakaoPay payment approval fails" + JSON_EN,
         {"T1": "P3", "T2": "P1", "T3": "P2", "T4": "P3", "T5": "P1"})
det_json("trx-convert-01", "convert", "medium",
         "아래 CSV를 {\"rows\": [...]} JSON으로 바꿔. 각 행은 객체이고, sku는 문자열, qty는 정수, price_krw는 정수, in_stock은 true/false 불리언으로 쓴다.\n\nsku,qty,price_krw,in_stock\nA-01,3,12000,yes\nB-07,0,8900,no\nC-12,15,45000,yes" + JSON_KO,
         "Convert the CSV below to JSON shaped {\"rows\": [...]}. Each row is an object; sku is a string, qty an integer, price_krw an integer, in_stock a true/false boolean.\n\nsku,qty,price_krw,in_stock\nA-01,3,12000,yes\nB-07,0,8900,no\nC-12,15,45000,yes" + JSON_EN,
         {"rows": [{"sku": "A-01", "qty": 3, "price_krw": 12000, "in_stock": True}, {"sku": "B-07", "qty": 0, "price_krw": 8900, "in_stock": False}, {"sku": "C-12", "qty": 15, "price_krw": 45000, "in_stock": True}]})
det_json("trx-convert-02", "convert", "hard",
         "오늘은 2026-10-05(월)이다. 아래 일정 표현을 YYYY-MM-DD로 바꿔 {\"1\": 날짜, …}로 출력해. '다음 주'는 오늘이 속한 월~일 주의 다음 주다. 연도가 없으면 오늘 이후 가장 가까운 날짜다.\n1. 모레\n2. 이번 주 금요일\n3. 다음 주 월요일\n4. 12월 25일\n5. 1월 3일" + JSON_KO,
         "Today is 2026-10-05 (Monday). Convert each expression to YYYY-MM-DD and output {\"1\": date, …}. 'Next week' is the Monday-to-Sunday week after the current one. A date without a year means its next occurrence on or after today.\n1. the day after tomorrow\n2. Friday this week\n3. Monday next week\n4. December 25\n5. January 3" + JSON_EN,
         {"1": "2026-10-07", "2": "2026-10-09", "3": "2026-10-12", "4": "2026-12-25", "5": "2027-01-03"})
item("transform", "trx-edit-01", "transform", "edit", "medium", "writing", "both",
     side("아래 메모를 고객에게 보낼 공손한 안내 문자로 다듬어 줘. 사실(금액, 날짜, 계좌)은 바꾸지 마.\n\n\"환불 금액 23,400원임. 10월 8일까지 들어감. 계좌는 신청하신 국민은행으로. 늦으면 연락 줘.\"",
          rubric(("사실 보존", "금액·날짜·계좌를 그대로 유지했는가", 0.45), ("어조", "고객에게 맞는 공손한 문체인가", 0.35), ("간결성", "문자에 맞게 짧은가", 0.2)),
          constraints({"kind": "require", "values": ["23,400원", "10월 8일", "국민은행"]}, {"kind": "max_chars", "n": 250})),
     side("Turn this note into a polite text message to the customer. Do not change the facts (amount, date, bank).\n\n\"refund 23,400 won. in by Oct 8. goes to the KB Kookmin account you gave. ping us if late.\"",
          rubric(("Facts kept", "Keeps the amount, date and bank unchanged", 0.45), ("Tone", "Polite and customer-appropriate", 0.35), ("Brevity", "Short enough for a text", 0.2)),
          constraints({"kind": "require", "values": ["23,400 won", "Oct", "Kookmin"]}, {"kind": "max_chars", "n": 400})))
item("transform", "trx-edit-02", "transform", "edit", "hard", "writing", "both",
     side("아래 공지를 핵심만 남겨 300자 이내로 다듬어 줘. 날짜·시간·대상·해야 할 일은 빠뜨리지 마.\n\n\"안녕하세요, 총무팀입니다. 다름이 아니오라 이번에 건물 관리사무소로부터 연락을 받았는데요, 10월 17일 금요일 저녁 8시부터 다음 날인 10월 18일 토요일 오전 6시까지 건물 전체 전기 설비 점검이 있을 예정이라고 합니다. 이에 따라 해당 시간 동안에는 엘리베이터 운행이 중단되며 사무실 전원도 차단됩니다. 그러므로 3층과 4층을 사용하시는 모든 임직원께서는 17일 금요일 퇴근 전에 반드시 개인 PC를 종료해 주시고, 냉장고에 보관 중인 음식물은 미리 가져가 주시기 바랍니다. 협조해 주셔서 감사합니다.\"",
          rubric(("정보 보존", "일시·대상·할 일을 빠짐없이 담았는가", 0.5), ("간결성", "불필요한 말이 없는가", 0.3), ("가독성", "한눈에 읽히는가", 0.2)),
          constraints({"kind": "max_chars", "n": 300}, {"kind": "require", "values": ["10월 17일", "10월 18일", "PC"]})),
     side("Trim the notice below to its essentials in under 500 characters. Keep the dates, times, who is affected and what to do.\n\n\"Hello, this is General Affairs. We were recently contacted by the building management office, and we've been told that there will be a building-wide electrical inspection from 8 pm on Friday, October 17 until 6 am the following day, Saturday, October 18. As a result, the elevators will be out of service and power to the offices will be cut during that time. Therefore, all staff on the 3rd and 4th floors are kindly asked to make sure to shut down their personal PCs before leaving on Friday the 17th, and to take home any food stored in the refrigerator in advance. Thank you for your cooperation.\"",
          rubric(("Information kept", "Keeps the times, who is affected and the actions", 0.5), ("Brevity", "No filler", 0.3), ("Readability", "Readable at a glance", 0.2)),
          constraints({"kind": "max_chars", "n": 500}, {"kind": "require", "values": ["October 17", "October 18", "PC"]})))
item("transform", "trx-translate-01", "transform", "translate", "hard", "writing", "judge",
     side("다음 계약 조항을 영어로 번역해 줘. 법률 용어의 정확성이 가장 중요해.\n\n\"제12조(손해배상) ① 당사자 일방이 본 계약을 위반하여 상대방에게 손해를 입힌 경우, 그 손해를 배상하여야 한다. 다만, 고의 또는 중과실이 없는 경우 배상액은 직전 12개월간 수령한 대금 총액을 한도로 한다. ② 간접손해 및 일실이익은 배상 범위에서 제외한다.\"",
          rubric(("법률 정확성", "손해배상·고의·중과실·간접손해·일실이익·책임 한도를 정확한 영문 법률 용어로 옮겼는가", 0.55), ("충실성", "조건과 예외 구조를 빠짐없이 보존했는가", 0.3), ("문체", "계약서다운 영어 문체인가", 0.15))),
     side("Translate this contract clause into Korean. Accuracy of legal terms matters most.\n\n\"Article 12 (Damages) (1) If either party breaches this Agreement and causes damage to the other party, it shall compensate for such damage; provided, however, that absent willful misconduct or gross negligence, the compensation shall be capped at the total fees received during the preceding twelve (12) months. (2) Indirect damages and lost profits are excluded from compensation.\"",
          rubric(("Legal accuracy", "Renders damages, willful misconduct, gross negligence, indirect damages, lost profits and the liability cap with correct Korean legal terms", 0.55), ("Fidelity", "Preserves the condition-and-exception structure", 0.3), ("Register", "Reads like a Korean contract", 0.15))))

if __name__ == "__main__":
    for group, items in OUT.items():
        path = SRC / f"{group}-expansion.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n")
        print(f"wrote {len(items)} items ({len(items) * 2} tasks) to {path.relative_to(ROOT)}")
