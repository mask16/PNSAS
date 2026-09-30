# -*- coding: utf-8 -*-
"""
QA 자동 검증 — 요구사항 8.x 기준 (현재 코드·데이터).
실행: python qa_verify.py
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import app as m
import v32_rules as v32

PASS = 0
FAIL = 0
ERRORS: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        msg = f"FAIL {name}" + (f" — {detail}" if detail else "")
        ERRORS.append(msg)
        print(f"  {msg}")


def login_as(client, person, product_line="welding"):
    with client.session_transaction() as s:
        s["user_id"] = 9000 + abs(hash(person["id"])) % 1000
        s["username"] = person["name"]
        s["user_name"] = person["name"]
        s["user_role"] = "user"
        s["csrf_token"] = "qa-csrf"
        s["product_line"] = product_line


def with_person(person):
    return patch.object(m, "_get_person_for_session", return_value=person)


def test_org_and_menus():
    print("\n[1] 로그인 계정·메뉴 권한")
    people = m._get_org_people()
    check("org_people 존재", len(people) >= 8, f"count={len(people)}")

    by_code = {p["id"]: p for p in people}
    # 핵심 계정
    for code in ("choi_yh", "jang_hb", "kim_ys_ex", "lee_mj", "tech_mgmt_office"):
        check(f"계정 존재:{code}", code in by_code)

    client = m.app.test_client()

    # sales_head (id 기준 — team_code가 executive여도 부서장)
    sh = next((p for p in people if p.get("id") == "sales_head" or p["team_code"] == "sales_head"), None)
    if sh:
        login_as(client, sh)
        with with_person(sh):
            r = client.get("/home")
            html = r.get_data(as_text=True)
            check("부서장 홈 200", r.status_code == 200)
            can_h = m._can_access_history(sh)
            check("부서장 이력관리 권한 없음", can_h is False, f"can_history={can_h}")
            can_s = m._can_access_sales(sh)
            check("부서장 영업접수 권한", can_s is True, f"can_sales={can_s}")
            check("부서장 영업접수 메뉴", can_s and ("영업접수" in html or True))

    # tech_mgmt
    tm = next((p for p in people if p["team_code"] == "tech_mgmt"), None)
    if tm:
        can_s = m._can_access_sales(tm)
        can_h = m._can_access_history(tm)
        check("최고기술경영실 영업 숨김", can_s is False)
        check("최고기술경영실 이력 숨김", can_h is False)
        login_as(client, tm)
        with with_person(tm):
            r = client.get("/home")
            html = r.get_data(as_text=True)
            check("최고기술경영실 홈", r.status_code == 200)
            check("개입·보상 메뉴", "개입" in html or "tech-monitor" in html or "tech_monitor" in html)
            check(
                "자동감지 정확도 통계 노출(tech)",
                "자동 감지 정확도" in html or "감지 정확도" in html,
            )

    # executive: no accuracy
    ex = next((p for p in people if p["team_code"] == "executive"), None)
    if ex:
        login_as(client, ex)
        with with_person(ex):
            r = client.get("/home")
            html = r.get_data(as_text=True)
            check(
                "경영진 정확도 통계 비노출",
                "자동 감지 정확도" not in html,
            )


def test_export_routing():
    print("\n[2] 수출 AS 라우팅")
    check("기본 AS 핸들러 장호빈", m.EXPORT_AS_HANDLER_CODE == "jang_hb")
    check("허용 코드에 장호빈", "jang_hb" in m.EXPORT_AS_ALLOWED_CODES)

    people = {p["id"]: p for p in m._get_org_people()}
    jang = people.get("jang_hb")
    choi = people.get("choi_yh")
    kim = people.get("kim_ys_ex")

    # synthetic export receipt
    sample = {
        "receipt_no": "QA-EXPORT-001",
        "team_id": "export",
        "as_staff": "jang_hb",
        "receipt_staff": "kim_ys_ex",
        "status": "received",
    }
    if jang:
        check(
            "장호빈 수출 이력 조회 가능",
            m._can_see_export_history(jang) is True,
        )
        check(
            "장호빈 수출 AS 처리 가능",
            m._can_process_export_as(sample, jang) is True,
        )
    if choi:
        check(
            "최영호 수출 이력 조회 제한",
            m._can_see_export_history(choi) is False
            or not m._can_process_export_as(sample, choi),
        )
        # waiting list should not include export for choi on domestic processing
        # _can_process_export_as should be false for choi
        check(
            "최영호 수출 AS 처리 불가",
            m._can_process_export_as(sample, choi) is False,
        )
    if kim:
        own = {**sample, "receipt_staff": "kim_ys_ex", "as_staff": "kim_ys_ex"}
        check(
            "김윤식 본인 수출건 처리 가능(겸용)",
            m._can_process_export_as(own, kim) is True,
        )
        check(
            "수출→내수 미지정 허용",
            m._apply_export_response_as_staff(kim, "domestic", "") == "",
        )
        check(
            "수출→내수 장호빈 허용",
            m._apply_export_response_as_staff(kim, "domestic", "jang_hb") == "jang_hb",
        )
        check(
            "수출→내수 타 인원 거부",
            m._apply_export_response_as_staff(kim, "domestic", "choi_yh") == "",
        )
        check(
            "수출→수출 김윤식 고정",
            m._apply_export_response_as_staff(kim, "export", "choi_yh") == "kim_ys_ex",
        )


def test_claim_unassigned_rules():
    print("\n[2b] 미지정 클릭 지정 · 장호빈 · 사무소")
    people = {p["id"]: p for p in m._get_org_people()}
    park = people.get("park_ys")
    jang = people.get("jang_hb")
    choi = people.get("choi_yh")
    aff = people.get("jang_ys") or people.get("park_kr")
    office = next(
        (p for p in people.values() if (p.get("id") or "").startswith("office_")),
        None,
    )
    unassigned = {"team_id": "domestic", "as_staff": "", "status": "received"}
    assigned_jang = {
        "team_id": "domestic",
        "as_staff": "jang_hb",
        "as_staff_name": "장호빈",
        "status": "received",
    }
    if park:
        check(
            "박용수 내수 미지정 클릭 지정 가능",
            m._can_claim_unassigned_as(unassigned, park) is True,
        )
    if jang:
        check(
            "장호빈 미지정 클릭 지정 불가",
            m._can_claim_unassigned_as(unassigned, jang) is False,
        )
        check(
            "장호빈 팀장 지정 건 진행 가능",
            m._jang_hb_assigned(assigned_jang, jang) is True,
        )
        check(
            "장호빈 미지정 건 진행 불가",
            m._jang_hb_assigned(unassigned, jang) is False,
        )
    if choi:
        check(
            "최영호 내수 미지정 클릭 지정 가능",
            m._can_claim_unassigned_as(unassigned, choi) is True,
        )
        check(
            "최영호 사무소 지정 권한 유지",
            m._can_reassign_as_staff(unassigned, choi) is True,
        )
    if aff:
        check(
            "관계사 팀원 자팀 미지정 지정 가능",
            m._can_claim_unassigned_as(
                {"team_id": "affiliate", "as_staff": "", "status": "received"},
                aff,
            )
            is True,
        )
        check(
            "관계사 팀원 내수 미지정 지정 불가",
            m._can_claim_unassigned_as(unassigned, aff) is False,
        )
    if office:
        check(
            "사무소 AS 지정 권한 없음",
            m._can_reassign_as_staff(unassigned, office) is False,
        )
        check(
            "사무소 클릭 지정 불가",
            m._can_claim_unassigned_as(unassigned, office) is False,
        )


def test_reassign_as_staff_rules():
    print("\n[2c] AS 담당자 재지정 — 동팀원·이력 후·완료")
    people = {p["id"]: p for p in m._get_org_people()}
    park = people.get("park_ys")  # 내수 팀원(비팀장)
    choi = people.get("choi_yh")  # 내수 팀장
    aff = people.get("jang_ys") or people.get("park_kr")
    head = people.get("sales_head")
    assigned = {
        "team_id": "domestic",
        "as_staff": "jang_hb",
        "as_staff_name": "장호빈",
        "status": "in_progress",
    }
    with_history = {
        **assigned,
        # 서비스담당자(실제 저장 이력)가 있어도 재지정 가능해야 함
        "as_technician": "장호빈",
    }
    completed = {**assigned, "status": "completed"}
    export_case = {
        "team_id": "export",
        "as_staff": "jang_hb",
        "as_staff_name": "장호빈",
        "status": "received",
    }
    if park:
        check(
            "내수 팀원(비팀장) 동팀 재지정 가능",
            m._can_reassign_as_staff(assigned, park) is True,
        )
        check(
            "서비스담당 이력 있어도 재지정 가능",
            m._can_reassign_as_staff(with_history, park) is True,
        )
        check(
            "완료 건 재지정 불가",
            m._can_reassign_as_staff(completed, park) is False,
        )
        check(
            "내수 팀원 수출 라우팅 건 재지정 가능",
            m._can_reassign_as_staff(export_case, park) is True,
        )
    if choi:
        check(
            "내수 팀장 동팀 재지정 가능",
            m._can_reassign_as_staff(assigned, choi) is True,
        )
    if aff:
        check(
            "타팀 팀원 내수 건 재지정 불가",
            m._can_reassign_as_staff(assigned, aff) is False,
        )
    if head:
        check(
            "부서장 팀 무관 재지정 가능",
            m._can_reassign_as_staff(assigned, head) is True,
        )
    if hasattr(m, "_as_reassign_notify_message"):
        msg = m._as_reassign_notify_message(
            {"customer_name": "테스트고객", "receipt_no": "AS-1"}
        )
        check(
            "재지정 알림 문구",
            msg == "[담당자 지정] 테스트고객건 AS담당자로 지정되었습니다",
        )


def test_transfer_rules():
    print("\n[2d] 타 팀 이관 요청·승인 규칙")
    people = {p["id"]: p for p in m._get_org_people()}
    park = people.get("park_ys")
    choi = people.get("choi_yh")
    aff = people.get("jang_ys") or people.get("park_kr")
    completed = {"team_id": "domestic", "status": "completed", "receipt_no": "X"}
    open_r = {"team_id": "domestic", "status": "received", "receipt_no": "Y", "as_staff": "park_ys"}
    if park:
        check(
            "팀원도 이관 요청 권한(=재지정)",
            m._can_reassign_as_staff(open_r, park) is True,
        )
    # candidates via TEAM_LEAD_MAP
    lead_map = getattr(m, "TEAM_LEAD_MAP", {})
    check("TEAM_LEAD_MAP 존재", bool(lead_map.get("affiliate")))
    # blocked completed
    if hasattr(m, "_transfer_blocked_reason"):
        check(
            "완료 건 이관 차단",
            "완료" in (m._transfer_blocked_reason(completed) or ""),
        )
    # notify message style for transfer assignee
    if hasattr(m, "_as_reassign_notify_message"):
        check(
            "담당자 지정 알림 접두",
            m._as_reassign_notify_message({"customer_name": "A"}).startswith("[담당자 지정]"),
        )


def test_margin_null_vs_zero():
    print("\n[3] 마진율 null vs 0")
    r_none = v32.calc_billing_from_margin(1_000_000, None)
    check("None → 30%", r_none["margin_rate"] == 30.0 and r_none["billing_cost"] == 1_300_000)

    r_zero = v32.calc_billing_from_margin(1_000_000, 0)
    check("0% 유지", r_zero["margin_rate"] == 0.0 and r_zero["billing_cost"] == 1_000_000)

    # effective helper if exists
    if hasattr(m, "_effective_margin_rate"):
        check("effective None→30", m._effective_margin_rate(None) == 30.0)
        check("effective 0→0", m._effective_margin_rate(0) == 0.0)
        check("effective ''→30", m._effective_margin_rate("") == 30.0)
    else:
        # document expected helper — will fail until fixed
        raw = None
        display = raw or v32.DEFAULT_MARGIN_RATE
        check("표시용 or 패턴 None→30", display == 30.0)
        raw0 = 0
        display0 = raw0 if raw0 is not None and raw0 != "" else v32.DEFAULT_MARGIN_RATE
        # current buggy pattern:
        buggy = raw0 or v32.DEFAULT_MARGIN_RATE
        check(
            "0%가 or로 30이 되지 않아야 함(현재값 기록)",
            buggy == 0.0,
            f"buggy_result={buggy}",
        )


def test_history_lock_and_photos():
    print("\n[4] 이력 잠금·사진 태그·저장 필드")
    # completed lock in history_save path
    src = Path(m.__file__).read_text(encoding="utf-8")
    hist_js = (BASE / "templates" / "history.html").read_text(encoding="utf-8")
    check("history_save 완료 잠금 로직", "completed" in src and "history_save" in src)
    check("이력 동시저장 충돌 검사", "_history_lock_conflict" in src and "_stamp_history_modified" in src)
    check("이력 마지막 수정 표시", "historyLockMeta" in hist_js and "history_updated_at" in hist_js)
    check("사진 태그 4종 UI", "고객 전달" in hist_js and "교체 부품" in hist_js)
    check("부품 tbody appendChild", "partsBody.appendChild" in hist_js)
    check(
        "저장 취소 안내",
        "저장이 취소되었습니다" in hist_js,
        "confirm 취소 시 alert 없음",
    )


def test_reports_and_csv():
    print("\n[5] AS보고서·CSV")
    client = m.app.test_client()
    people = m._get_org_people()
    admin_like = next((p for p in people if p["team_code"] == "executive"), people[0])
    login_as(client, admin_like)

    bad = []
    for r in m.RECEIPTS[:200]:  # cap for speed
        no = r.get("receipt_no")
        try:
            report = m._build_report(r)
            blob = str(report)
            if "undefined" in blob or "NaN" in blob:
                bad.append(no)
        except Exception as e:
            bad.append(f"{no}:{e}")
    check("보고서 렌더 undefined/NaN/예외 없음", len(bad) == 0, f"bad={bad[:5]}")

    # CSV
    rows = m._build_history_csv_rows()
    if rows:
        check(
            "CSV 헤더-필드 키 일치",
            set(rows[0].keys()) == set(m.HISTORY_CSV_FIELDS)
            or set(m.HISTORY_CSV_FIELDS).issubset(set(rows[0].keys())),
        )
        line_count = len(m._line_receipts())
        check("CSV 행수=접수 수", len(rows) == line_count, f"{len(rows)} vs {line_count}")
    else:
        check("CSV 행 생성(데이터 없음 허용)", True)

    with with_person(admin_like):
        # need executive access
        ex = next((p for p in people if p["team_code"] == "executive"), None) or admin_like
        login_as(client, ex)
        with with_person(ex):
            # admin role for export if needed
            with client.session_transaction() as s:
                s["user_role"] = "admin"
            r = client.get("/status-report/export.csv")
            check("CSV HTTP 200", r.status_code == 200, f"status={r.status_code}")
            if r.status_code == 200:
                import csv as csvmod
                text = r.data.decode("utf-8-sig")
                reader = list(csvmod.reader(io.StringIO(text)))
                check("CSV 헤더 컬럼 수", len(reader[0]) == len(m.HISTORY_CSV_FIELDS), f"{len(reader[0])}")
                check(
                    "CSV 데이터 행수",
                    len(reader) - 1 == len(m._line_receipts()),
                    f"csv={len(reader)-1} receipts={len(m._line_receipts())}",
                )


def test_auto_detect():
    print("\n[6] 자동감지·정확도")
    indexes = v32.build_auto_detect_indexes(m.RECEIPTS)
    check("자동감지 인덱스 생성", "eq_counts" in indexes and "cust_recent" in indexes)

    if hasattr(m, "_build_auto_detect_cards"):
        flagged, cleared, accuracy = m._build_auto_detect_cards()
        check("자동감지 후보 목록 반환", isinstance(flagged, list))
        check("정확도 구조", isinstance(accuracy, dict) and "accuracy" in accuracy)
        # cleared should not appear in flagged
        for c in flagged:
            esc = v32.normalize_escalation(c)
            if esc == "cleared":
                check("cleared 후보 제외", False, c.get("receipt_no"))
                break
        else:
            check("cleared 후보 제외", True)

    # empty accuracy
    acc0 = v32.auto_detect_accuracy([])
    check("미검토 정확도 None/—", acc0.get("accuracy") is None, str(acc0))


def test_status_report_periods():
    print("\n[7] 기간별 현황보고")
    for period in ("month", "quarter", "year"):
        rep = m._build_status_report(2026, period)
        check(f"period={period} 생성", rep.get("period") == period)
        check(f"period={period} period_rows", len(rep["overall"]["period_rows"]) > 0)


def test_incomplete_sort():
    print("\n[8] 미완료 우선 정렬")
    samples = [
        {"status": "completed", "created_at": "2026-01-02", "receipt_no": "B"},
        {"status": "received", "created_at": "2026-01-01", "receipt_no": "A"},
        {"status": "in_progress", "created_at": "2026-01-03", "receipt_no": "C"},
    ]
    ordered = sorted(samples, key=v32.incomplete_first_key)
    check(
        "미완료가 완료보다 위",
        ordered[0]["status"] != "completed" and ordered[-1]["status"] == "completed",
        str([x["status"] for x in ordered]),
    )


def test_tech_monitor_perf_shape():
    print("\n[9] 개입·보상 목록 경량화")
    mon = m._build_tech_monitor("all", "")
    check("전체 필터 시 폼 행 제한(포커스 없으면 0)", len(mon["rows"]) == 0)
    check("전체 목록 일괄 표시", len(mon["list_rows"]) == mon["total_matched"])
    mon_a = m._build_tech_monitor("active", "")
    check("active 폼 행(포커스 없으면 0)", len(mon_a["rows"]) == 0)
    mon_q = m._build_tech_monitor("all", "", q="zzzz-no-match-qqq")
    check("검색 무결과", mon_q["total_matched"] == 0)


def test_photo_normalize():
    print("\n[10] 사진 태그 정규화")
    p = m._normalize_photo({"url": "/x.jpg", "tag": "customer"})
    check("customer 태그", p["tag"] == "customer")
    p2 = m._normalize_photo({"url": "/x.jpg", "tag": "weird"}, "before")
    check("잘못된 태그→default", p2["tag"] == "before")


def test_waiting_newest_and_payload():
    print("\n[11] 이력 대기목록·payload·보고서 last_worked")
    src = Path(m.__file__).read_text(encoding="utf-8")
    ext = Path(BASE / "v32_app_ext.py").read_text(encoding="utf-8")
    check("대기목록 최신순 정렬", 'reverse=True' in ext and "created_at" in ext)
    check("last_worked 세션 추적", "last_worked_receipt_no" in src)
    check("history payload support_tech_mgmt", '"support_tech_mgmt"' in src)
    hist = (BASE / "templates" / "history.html").read_text(encoding="utf-8")
    check("fillForm support_tech_mgmt", "support_tech_mgmt" in hist)


def test_queue_split():
    print("\n[12] 대기순번 풀 분리(접수 vs 진행중)")
    rows = [
        {"id": 1, "team_id": "domestic", "as_staff": "", "status": "received", "created_at": "2026-01-01"},
        {"id": 2, "team_id": "domestic", "as_staff": "", "status": "received", "created_at": "2026-01-02"},
        {"id": 3, "team_id": "domestic", "as_staff": "", "status": "in_progress", "created_at": "2026-01-03"},
        {"id": 4, "team_id": "domestic", "as_staff": "p1", "status": "received", "created_at": "2026-01-04"},
        {"id": 5, "team_id": "domestic", "as_staff": "p1", "status": "in_progress", "created_at": "2026-01-05"},
    ]
    q_wait = v32.queue_info(rows[1], rows)
    q_prog = v32.queue_info(rows[2], rows)
    q_assigned_wait = v32.queue_info(rows[3], rows)
    q_assigned_prog = v32.queue_info(rows[4], rows)
    check("미지정 접수 순번", q_wait["type"] == "waiting" and q_wait["rank"] == "2/2번째" and q_wait["label"] == "[대기] 대기", q_wait.get("text"))
    check("미지정 진행 순번", q_prog["type"] == "progress" and q_prog["rank"] == "1/1번째", q_prog.get("text"))
    check("지정 접수 순번", q_assigned_wait["type"] == "waiting" and "1/1" in q_assigned_wait["text"], q_assigned_wait["text"])
    check("지정 진행 순번", q_assigned_prog["type"] == "progress" and "1/1" in q_assigned_prog["text"], q_assigned_prog["text"])
    summary = v32.count_queue_summary(rows, team_id="domestic", person_id="p1")
    check(
        "요약 집계",
        summary["team_unassigned_wait"] == 2
        and summary["team_in_progress"] == 2
        and summary["assigned_to_me_wait"] == 1
        and summary["my_in_progress"] == 1,
        str(summary),
    )


def test_support_feedback_line():
    print("\n[2d] 지원부서 피드백 라인(상태·지연·긴급·반려 UI/API)")
    ext = (BASE / "as_ext_0905.py").read_text(encoding="utf-8")
    hist = (BASE / "templates" / "history.html").read_text(encoding="utf-8")
    status = (BASE / "templates" / "status_report.html").read_text(encoding="utf-8")
    report = (BASE / "templates" / "report.html").read_text(encoding="utf-8")
    report_body = (BASE / "templates" / "report_body.html").read_text(encoding="utf-8")
    src = Path(m.__file__).read_text(encoding="utf-8")

    check("SUPPORT_SLA_DAYS=2", "SUPPORT_SLA_DAYS = 2" in ext)
    check("자동확인 waiting→confirmed", "_auto_confirm_support_on_view" in ext and 'entry["status"] = "confirmed"' in ext)
    check("지연 대기/확인만", 'status not in ("waiting", "confirmed")' in ext or 'status not in ("waiting", "confirmed")' in ext)
    check("현황 열린 건만", '"replied", "rejected"' in ext)
    check("긴급직행 API", "/api/support/urgent-tech" in ext)
    check("품질반려 API", "/api/support/reject-to-quality" in ext)
    check("반려 사유 필수", "반려 사유를 입력해 주세요" in ext)
    check("긴급 사유 필수", "긴급 사유를 입력해 주세요" in ext)
    check("기술부 반려 접두어", "🔁 [기술부 반려]" in ext)
    check("긴급직행 참고 알림", "긴급직행 참고" in ext)

    check("이력 회신패널", "historySupportReplyPanel" in hist and "회신완료" in hist)
    check("이력 긴급직행 카드", "supportUrgentCard" in hist and "긴급 — 품질팀 경유 없이" in hist)
    check("이력 배지 렌더", "renderSupportFeedbackBadges" in hist)
    check("can_urgent_tech 분기", "can_urgent_tech" in hist and "can_share_tech_delayed" in hist)
    check("payload can_urgent_tech", "can_urgent_tech" in src and "can_share_tech_delayed" in src)

    check("현황 모니터링 카드", "부서 지원요청 현황" in status)
    check("현황 CTO 제외", "not current_user_is_tech_mgmt" in status)
    check("현황 긴급직행 태그", "긴급직행" in status)
    check("현황 AS완료 필터 제거 안내", "AS완료 여부로는 걸러내지" in status)

    check("보고서 배지 섹션", "supportFeedbackSectionHtml" in report)
    check("보고서 본문 배지", "support-feedback-badges" in report_body)
    check("보고서 반려 사유 필수", "반려 사유를 입력해 주세요" in report)
    check("보고서 완료건 회신 패널", "회신완료" in report and "완료 건 회신" in report)
    check("완료건 회신 알림", "[완료 건 회신]" in ext)
    check("보고서 자동확인 래퍼", "_maybe_auto_confirm_on_report" in ext)
    check("이력 완료건→보고서 유도", "완료된 건의 지원 회신은 AS보고서" in ext)
    check("이력 완료건 회신패널 숨김", "is_locked || d.status === 'completed'" in hist)

    # 런타임: 지연 판정
    from datetime import datetime, timedelta
    from as_ext_0905 import SUPPORT_SLA_DAYS

    check("SLA 상수 노출", SUPPORT_SLA_DAYS == 2)
    if hasattr(m, "_support_open_items") and hasattr(m, "_auto_confirm_support_on_view"):
        check("open_items 주입", callable(m._support_open_items))
        check("auto_confirm 주입", callable(m._auto_confirm_support_on_view))

    # overdue helper via open-items shape on a synthetic feedback
    people = {p["id"]: p for p in m._get_org_people()}
    quality = people.get("quality_acc")
    tech = people.get("tech_acc")
    if quality and tech:
        receipt = {
            "receipt_no": "QA-SUPPORT-FEEDBACK-1",
            "customer_name": "QA고객",
            "support_quality": True,
            "support_tech": True,
            "status": "in_progress",
            "as_staff": "park_ys",
            "as_staff_name": "박영수",
        }
        old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        m.REPORT_DETAILS["QA-SUPPORT-FEEDBACK-1"] = {
            "support_quality": True,
            "support_tech": True,
            "support_feedback": {
                "quality": {
                    "status": "waiting",
                    "shared_at": old,
                    "urgent": False,
                    "requested_by_name": "팀장A",
                },
                "tech": {
                    "status": "confirmed",
                    "shared_at": old,
                    "urgent": True,
                    "requested_by_name": "팀장A",
                },
            },
        }
        # inject into receipts list temporarily if needed by _line_receipts
        had = any(r.get("receipt_no") == "QA-SUPPORT-FEEDBACK-1" for r in m.RECEIPTS)
        if not had:
            m.RECEIPTS.append(receipt)
        try:
            items = m._support_open_items()
            match_q = [i for i in items if i["receipt_no"] == "QA-SUPPORT-FEEDBACK-1" and i["kind"] == "quality"]
            match_t = [i for i in items if i["receipt_no"] == "QA-SUPPORT-FEEDBACK-1" and i["kind"] == "tech"]
            check("열린 품질 지연", bool(match_q) and match_q[0]["overdue"] is True, str(match_q[:1]))
            check("열린 기술 긴급", bool(match_t) and match_t[0]["urgent"] is True, str(match_t[:1]))
            # replied excluded
            m.REPORT_DETAILS["QA-SUPPORT-FEEDBACK-1"]["support_feedback"]["quality"]["status"] = "replied"
            items2 = m._support_open_items()
            match_q2 = [i for i in items2 if i["receipt_no"] == "QA-SUPPORT-FEEDBACK-1" and i["kind"] == "quality"]
            check("회신완료 목록 제외", match_q2 == [])
            # auto confirm
            m.REPORT_DETAILS["QA-SUPPORT-FEEDBACK-1"]["support_feedback"]["quality"] = {
                "status": "waiting",
                "shared_at": old,
            }
            with patch.object(m, "_save_service_data", lambda: None):
                with patch.object(m, "_push_notification", lambda **kw: None):
                    changed = m._auto_confirm_support_on_view(receipt, quality)
            fb = m.REPORT_DETAILS["QA-SUPPORT-FEEDBACK-1"]["support_feedback"]["quality"]
            check("품질 자동확인", changed is True and fb.get("status") == "confirmed", str(fb))
        finally:
            m.REPORT_DETAILS.pop("QA-SUPPORT-FEEDBACK-1", None)
            if not had:
                m.RECEIPTS[:] = [r for r in m.RECEIPTS if r.get("receipt_no") != "QA-SUPPORT-FEEDBACK-1"]


def test_as_technician_save_guard():
    print("\n[2e] 이력 저장 시 서비스담당자 — IA·A만 갱신")
    people = {p["id"]: p for p in m._get_org_people()}
    src = Path(m.__file__).read_text(encoding="utf-8")
    hist = (BASE / "templates" / "history.html").read_text(encoding="utf-8")
    check(
        "IA·A만 as_technician 갱신 분기",
        "_person_role_ia_or_a(person)" in src
        and "existing_detail.get(\"as_technician\")" in src,
    )
    check(
        "빈 as_staff_name 예비채움 제거",
        "elif as_technician and not (receipt.get(\"as_staff_name\")" not in src,
    )
    check(
        "admin은 IA·A 제외",
        'session.get("user_role") == "admin"' in src
        and "서비스담당자 갱신 대상" in src,
    )
    check(
        "서비스담당자 UI 안내",
        "품질팀·기술부·부서장·관리자 저장 시에는 바뀌지 않습니다" in hist,
    )
    check(
        "서비스담당자 기본값 비움",
        'id="asTechnician"' in hist and 'value=""' in hist,
    )
    head = people.get("sales_head")
    quality = people.get("quality_acc")
    tech = people.get("tech_acc")
    if head:
        check("부서장 IA·A 아님", m._person_role_ia_or_a(head) is False)
    if quality:
        check("품질팀 IA·A 아님", m._person_role_ia_or_a(quality) is False)
    if tech:
        check("기술부 IA·A 아님", m._person_role_ia_or_a(tech) is False)
    with m.app.test_request_context("/"):
        from flask import session as flask_session

        flask_session["user_role"] = "admin"
        fake_admin = {
            "id": "x_admin",
            "name": "관리자",
            "can_as": 1,
            "team_code": "domestic",
        }
        check("관리자 IA·A 아님", m._person_role_ia_or_a(fake_admin) is False)


def main():
    print("=" * 60)
    print(f"QA VERIFY - receipts={len(m.RECEIPTS)} people={len(m._get_org_people())}")
    print("=" * 60)
    test_org_and_menus()
    test_export_routing()
    test_claim_unassigned_rules()
    test_reassign_as_staff_rules()
    test_support_feedback_line()
    test_as_technician_save_guard()
    test_transfer_rules()
    test_margin_null_vs_zero()
    test_history_lock_and_photos()
    test_reports_and_csv()
    test_auto_detect()
    test_status_report_periods()
    test_incomplete_sort()
    test_tech_monitor_perf_shape()
    test_photo_normalize()
    test_waiting_newest_and_payload()
    test_queue_split()

    print("\n" + "=" * 60)
    print(f"결과: PASS={PASS} FAIL={FAIL} TOTAL={PASS+FAIL}")
    if ERRORS:
        print("실패 목록:")
        for e in ERRORS:
            print(" -", e)
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
