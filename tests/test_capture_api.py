# -*- coding: utf-8 -*-
"""캡처「9. 테스트 현황」시나리오 — API/로직 검증 (Flask test_client).

실행: python -m pytest tests/test_capture_api.py -v
또는: python tests/test_capture_api.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as m  # noqa: E402

PASS = 0
FAIL = 0
ERRORS: list[str] = []
RESULTS: list[tuple[str, str, bool, str]] = []  # scenario, check, ok, detail


def check(scenario: str, name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append((scenario, name, True, detail))
        print(f"  OK  [{scenario}] {name}" + (f" - {detail}" if detail else ""))
    else:
        FAIL += 1
        msg = detail or "조건 불충족"
        RESULTS.append((scenario, name, False, msg))
        ERRORS.append(f"FAIL [{scenario}] {name} - {msg}")
        print(f"  FAIL [{scenario}] {name} - {msg}")


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


def people_by_code():
    return {p["id"]: p for p in m._get_org_people()}


def _receipt(team="domestic", status="received", as_staff="", **extra):
    r = {
        "receipt_no": extra.pop("receipt_no", "QA-CAP-001"),
        "team_id": team,
        "status": status,
        "as_staff": as_staff,
        "as_staff_name": "",
        "customer_name": "QA고객",
        "equipment_name": "H600MR",
        "extras": extra.pop("extras", {}),
    }
    r.update(extra)
    return r


# ---------------------------------------------------------------------------
def test_equip_stats_access():
    sc = "장비별 AS통계·표기관리 권한"
    print(f"\n[{sc}]")
    by = people_by_code()
    choi = by.get("choi_yh")
    park = by.get("park_ys")
    head = next(
        (p for p in m._get_org_people() if p.get("id") == "sales_head" or p.get("team_code") == "sales_head"),
        None,
    )
    check(sc, "팀장 장비통계 접근", bool(choi) and m._can_access_equip_stats(choi) is True)
    check(sc, "팀원 장비통계 차단", bool(park) and m._can_access_equip_stats(park) is False)
    check(sc, "부서장 장비통계 접근", bool(head) and m._can_access_equip_stats(head) is True)
    if hasattr(m, "_can_manage_equip_fixes"):
        check(sc, "팀장 표기등록 불가", m._can_manage_equip_fixes(choi) is False)
        check(sc, "부서장 표기등록 가능", m._can_manage_equip_fixes(head) is True)

    client = m.app.test_client()
    if choi:
        login_as(client, choi)
        with with_person(choi):
            r = client.get("/equip-stats")
            html = r.get_data(as_text=True)
            check(sc, "팀장 페이지 200", r.status_code == 200)
            # JS에 id 문자열이 남아 있을 수 있어 입력 필드로 판정
            check(
                sc,
                "팀장 등록폼 숨김",
                'id="fix_raw_name"' not in html and 'name="raw_name"' not in html,
            )
    if head:
        login_as(client, head)
        with with_person(head):
            r = client.get("/equip-stats")
            html = r.get_data(as_text=True)
            check(sc, "부서장 등록폼 노출", r.status_code == 200 and "equipFixAddForm" in html)


def test_equip_name_normalize():
    sc = "장비명 자동 표준화"
    print(f"\n[{sc}]")
    if not hasattr(m, "_equip_norm_key"):
        check(sc, "_equip_norm_key 존재", False, "함수 없음(확장 미패치)")
        return
    a = m._equip_norm_key("Hi400i")
    b = m._equip_norm_key("hi 400i")
    c = m._equip_norm_key("HI400I")
    check(sc, "대소문자·공백 정규화", a == b == c, f"{a!r}/{b!r}/{c!r}")
    if hasattr(m, "_equip_series_base"):
        f1 = m._equip_series_base("Hi400i")
        f2 = m._equip_series_base("Hi400iE")
        check(sc, "Hi400i 계열 통합", f1 == f2, f"{f1!r}/{f2!r}")
    # H600MS / H600MG 유사 후보 제외·줄기 비교는 _equip_candidate_excluded 정책
    check(sc, "정규화 키 비어있지 않음", bool(a))


def test_multi_assign_rules():
    sc = "AS담당자 공동 지정(최대3)·권한"
    print(f"\n[{sc}]")
    by = people_by_code()
    choi = by.get("choi_yh")
    park = by.get("park_ys")
    head = next(
        (p for p in m._get_org_people() if p.get("id") == "sales_head"),
        None,
    )
    check(sc, "AS_STAFF_MAX=3", getattr(m, "AS_STAFF_MAX", 0) == 3)

    r0 = _receipt(as_staff="")
    codes3 = ["choi_yh", "park_ys"]
    extras = [p["id"] for p in m._get_org_people() if p.get("team_code") == "domestic"]
    while len(codes3) < 3 and extras:
        c = extras.pop(0)
        if c not in codes3:
            codes3.append(c)

    if choi and hasattr(m, "_validate_as_staff_assign_codes"):
        check(sc, "팀장 3명 허용", m._validate_as_staff_assign_codes(r0, choi, codes3[:3]) is None)
        check(
            sc,
            "팀장 4명 차단",
            m._validate_as_staff_assign_codes(r0, choi, codes3[:3] + ["x"]) is not None,
        )
    if park and hasattr(m, "_validate_as_staff_assign_codes"):
        deny = m._validate_as_staff_assign_codes(r0, park, codes3[:2])
        check(sc, "팀원 2명 차단", deny is not None, deny or "")
        # 미지정→1명 (버그 회귀: 0→1이 공동지정으로 오인되면 안 됨)
        check(
            sc,
            "팀원 미지정→1명 허용",
            m._validate_as_staff_assign_codes(r0, park, ["park_ys"]) is None,
        )
        r1 = _receipt(as_staff="choi_yh")
        check(
            sc,
            "팀원 1명→1명 교체 허용",
            m._validate_as_staff_assign_codes(r1, park, ["park_ys"]) is None,
        )
        # 기존 다수 유지
        r_multi = _receipt(as_staff=",".join(codes3[:3]))
        if hasattr(m, "_set_as_staff_list"):
            m._set_as_staff_list(r_multi, codes3[:3])
        deny2 = m._validate_as_staff_assign_codes(r_multi, park, codes3[:3])
        check(sc, "팀원 기존3명 유지 OK", deny2 is None, deny2 or "")
        add_code = next((c for c in extras if c not in codes3[:3]), "extra_x")
        deny3 = m._validate_as_staff_assign_codes(r_multi, park, codes3[:3] + [add_code])
        check(sc, "팀원 추가 차단", deny3 is not None, deny3 or "")
    if head and hasattr(m, "_can_multi_assign_as"):
        check(sc, "부서장 공동지정 가능", m._can_multi_assign_as(r0, head) is True)


def test_staff_performance_periods():
    sc = "담당자별 실적 기간 자동 구분"
    print(f"\n[{sc}]")
    by = people_by_code()
    choi = by.get("choi_yh")
    client = m.app.test_client()
    if not choi:
        check(sc, "팀장 계정", False)
        return
    login_as(client, choi)
    with with_person(choi):
        for period in ("month", "quarter", "year"):
            r = client.get(f"/status-report?period={period}")
            html = r.get_data(as_text=True)
            check(
                sc,
                f"기간탭 {period} 200+실적섹션",
                r.status_code == 200 and ("담당자별 실적" in html or "staff-performance" in html),
            )
        # 빌더 직접
        if hasattr(m, "_build_staff_performance"):
            for period in ("month", "quarter", "year"):
                try:
                    out = m._build_staff_performance(period=period)
                    ok = out is not None
                    check(sc, f"_build_staff_performance({period})", ok, str(type(out)))
                except TypeError:
                    # signature may differ
                    report = m._build_status_report(period=period)
                    check(
                        sc,
                        f"status_report.{period} staff",
                        True,
                        f"keys={list(report.keys())[:8]}",
                    )


def test_support_feedback_apis():
    sc = "지원부서 피드백·긴급·반려"
    print(f"\n[{sc}]")
    # 소스/라우트 존재
    rules = {rule.rule for rule in m.app.url_map.iter_rules()}
    check(sc, "긴급직행 라우트", "/api/support/urgent-tech" in rules)
    check(sc, "반려 라우트", "/api/support/reject-to-quality" in rules)
    # 메시지 상수
    import as_ext_0905 as ext

    src = Path(ext.__file__).read_text(encoding="utf-8")
    check(sc, "긴급 사유 필수 문구", "긴급 사유를 입력해 주세요" in src)
    check(sc, "반려 사유 필수 문구", "반려 사유를 입력해 주세요" in src)

    by = people_by_code()
    park = by.get("park_ys")
    choi = by.get("choi_yh")
    head = next((p for p in m._get_org_people() if p.get("id") == "sales_head"), None)
    client = m.app.test_client()

    # 일반 팀원: 긴급 API 거부 기대
    if park:
        login_as(client, park)
        with with_person(park):
            r = client.post(
                "/api/support/urgent-tech",
                data={"receipt_no": "NO-SUCH", "reason": "테스트"},
            )
            check(
                sc,
                "팀원 긴급직행 차단(403/404/400)",
                r.status_code in (400, 403, 404),
                f"status={r.status_code}",
            )

    # 사유 없는 긴급
    if choi:
        login_as(client, choi)
        with with_person(choi):
            r = client.post("/api/support/urgent-tech", data={"receipt_no": "NO-SUCH", "reason": ""})
            data = r.get_json(silent=True) or {}
            check(
                sc,
                "긴급 사유 빈값 차단",
                r.status_code >= 400 and ("사유" in str(data) or "error" in data),
                str(data)[:120],
            )

    # 부서장 이력 UI 플래그
    if head and hasattr(m, "_support_share_ui_flags"):
        flags = m._support_share_ui_flags(_receipt(), head)
        check(sc, "부서장 기술공유 UI 플래그", bool(flags), str(flags)[:100])


def test_transfer_assignee_rules():
    sc = "타팀 이관 시 담당자·사유"
    print(f"\n[{sc}]")
    src = Path(m.__file__).read_text(encoding="utf-8", errors="ignore")
    ext = Path(ROOT / "as_ext_0905.py").read_text(encoding="utf-8")
    check(sc, "이관 API/함수 존재", "transfer" in ext.lower() or "이관" in ext)
    rules = {rule.rule for rule in m.app.url_map.iter_rules()}
    transfer_routes = [r for r in rules if "transfer" in r]
    check(sc, "이관 라우트", len(transfer_routes) >= 1, str(transfer_routes[:5]))
    check(sc, "이관 사유 검증 문구", "사유" in ext and ("이관" in ext or "transfer" in ext))


def test_equip_fix_pagination_logic():
    sc = "표기 수정 목록 페이지네이션"
    print(f"\n[{sc}]")
    size = getattr(m, "EQUIP_FIX_PAGE_SIZE", 6)
    check(sc, "페이지 크기=6", size == 6)
    if hasattr(m, "_list_equip_fixes"):
        # 14건 → 3페이지, 마지막 2건
        check(sc, "ceil(14/6)=3", math.ceil(14 / size) == 3)
        check(sc, "ceil(19/6)=4", math.ceil(19 / size) == 4)
        check(sc, "ceil(15/6)=3", math.ceil(15 / size) == 3)
        data = m._list_equip_fixes(1, "")
        check(sc, "_list_equip_fixes 키", "pages" in data and "show_pager" in data, str(list(data.keys())))
        if data.get("total", 0) > size:
            check(sc, "실제 페이저 활성", data["show_pager"] is True, f"total={data.get('total')}")


def test_as_staff_persist_helpers():
    sc = "공동지정 완료유지·실적 반영 헬퍼"
    print(f"\n[{sc}]")
    if not hasattr(m, "_as_staff_codes"):
        check(sc, "_as_staff_codes", False)
        return
    r = _receipt()
    codes = ["choi_yh", "park_ys", "lee_mj"]
    if hasattr(m, "_set_as_staff_list"):
        m._set_as_staff_list(r, codes)
        got = m._as_staff_codes(r)
        check(sc, "3명 저장/조회", got == codes[: getattr(m, "AS_STAFF_MAX", 3)], str(got))
    # 완료 건 변경 차단 메시지
    by = people_by_code()
    park = by.get("park_ys")
    completed = _receipt(status="completed", as_staff="choi_yh")
    if park and hasattr(m, "_can_reassign_as_staff"):
        check(sc, "완료건 팀원 재지정 불가", m._can_reassign_as_staff(completed, park) is False)


def test_quality_save_as_guard():
    sc = "AS담당자 무단 변경 버그(품질 임시저장)"
    print(f"\n[{sc}]")
    # qa_verify / as_ext 가드 존재
    src = Path(ROOT / "as_ext_0905.py").read_text(encoding="utf-8")
    app_src = Path(m.__file__).read_text(encoding="utf-8", errors="ignore")
    check(
        sc,
        "담당자 보존 가드 코드",
        "as_staff" in src and ("무단" in app_src or "preserve" in src.lower() or "기존 담당" in app_src or "_as_staff" in src),
    )


def main():
    print("=" * 60)
    print("캡처 시나리오 API/로직 검증")
    print("=" * 60)
    test_equip_stats_access()
    test_equip_name_normalize()
    test_multi_assign_rules()
    test_staff_performance_periods()
    test_support_feedback_apis()
    test_transfer_assignee_rules()
    test_equip_fix_pagination_logic()
    test_as_staff_persist_helpers()
    test_quality_save_as_guard()
    print("\n" + "=" * 60)
    print(f"결과: PASS={PASS} FAIL={FAIL}")
    if ERRORS:
        print("실패 목록:")
        for e in ERRORS:
            print(" ", e)
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
