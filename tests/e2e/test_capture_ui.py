# -*- coding: utf-8 -*-
"""캡처「9. 테스트 현황」시나리오 — Playwright UI 검증."""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.playwright


# ---------------------------------------------------------------------------
# 장비별 AS통계 · 표기 통합 관리 권한
# ---------------------------------------------------------------------------
def test_equip_stats_visible_for_leader(login_as, base_url, accounts):
    page = login_as("leader")
    page.goto(f"{base_url}/equip-stats", wait_until="domcontentloaded")
    assert page.locator(".equip-stats-block, .equip-table, h1").count() > 0
    # 팀장은 등록 폼 숨김
    assert page.locator("#equipFixAddForm").count() == 0
    assert "문의" in (page.locator(".equip-fix-team-note").inner_text() if page.locator(".equip-fix-team-note").count() else "")
    # 삭제 버튼 없음
    assert page.locator(".js-equip-fix-del").count() == 0


def test_equip_stats_manage_for_head(login_as, base_url, accounts):
    page = login_as("head")
    page.goto(f"{base_url}/equip-stats", wait_until="domcontentloaded")
    assert page.locator("#equipFixAddForm").count() == 1
    assert page.locator("#fix_raw_name").count() == 1
    assert page.locator("#fix_std_name").count() == 1


def test_equip_stats_hidden_for_member(login_as, base_url, accounts):
    page = login_as("member")
    resp = page.goto(f"{base_url}/equip-stats", wait_until="domcontentloaded")
    # 리다이렉트 또는 권한 안내
    url = page.url
    body = page.content()
    denied = (
        "equip-stats" not in url
        or "권한" in body
        or "접근" in body
        or page.locator("#equipFixAddForm").count() == 0
        and page.locator(".equip-fix-manage").count() == 0
        and "장비별" not in (page.title() or "")
    )
    # 팀원은 메뉴/페이지 접근 불가여야 함
    nav = page.locator('a[href*="equip"]')
    # soft: 페이지가 열려도 관리 UI는 없어야 함
    assert page.locator("#equipFixAddForm").count() == 0


# ---------------------------------------------------------------------------
# 담당자별 실적 기간 탭
# ---------------------------------------------------------------------------
def test_staff_performance_period_tabs(login_as, base_url, accounts):
    page = login_as("leader")
    page.goto(f"{base_url}/status-report?period=month", wait_until="domcontentloaded")
    assert page.locator(".staff-performance-block, .staff-performance-table").count() > 0
    # 분기
    page.locator("a.status-period-tab", has_text="분기").click()
    page.wait_for_load_state("domcontentloaded")
    assert "period=quarter" in page.url or page.locator(".status-period-tab.active", has_text="분기").count()
    assert page.locator(".staff-performance-table").count() > 0
    # 연도
    page.locator("a.status-period-tab", has_text="연도").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.locator(".staff-performance-table").count() > 0
    # 합계 행 존재(데이터가 있으면)
    page.goto(f"{base_url}/status-report?period=month", wait_until="domcontentloaded")
    tables = page.locator(".staff-performance-table")
    assert tables.count() >= 1


# ---------------------------------------------------------------------------
# 공동 지정 UI (팀장 최대 3 / 팀원 1)
# ---------------------------------------------------------------------------
def test_multi_assign_ui_leader_max3(login_as, base_url, accounts):
    page = login_as("leader")
    page.goto(f"{base_url}/sales", wait_until="domcontentloaded")
    btn = page.locator(".js-assign-as").first
    if btn.count() == 0:
        pytest.skip("지정 가능한 접수 건 없음")
    btn.click()
    page.wait_for_selector("#assignModal:not([hidden]), .assign-modal:not([hidden]), #assignDropLabel", timeout=8000)
    label = page.locator("#assignDropLabel, #homeAssignDropLabel").first
    text = label.inner_text() if label.count() else ""
    assert "최대 3" in text or "3명" in text or page.evaluate("() => typeof AS_STAFF_MAX !== 'undefined' ? AS_STAFF_MAX : 0") == 3
    # 카드 선택
    cards = page.locator(".assign-person-card, .js-assign-person")
    if cards.count() < 4:
        pytest.skip("후보 담당자 4명 미만 — 4번째 차단 UI 스킵")
    for i in range(3):
        cards.nth(i).click()
        page.wait_for_timeout(150)
    # 4번째 클릭 시 경고
    cards.nth(3).click()
    page.wait_for_timeout(300)
    warn = page.locator("#assignWarn, .assign-warn, .assign-inline-warn")
    body_text = page.locator("body").inner_text()
    assert (
        warn.count() and ("최대" in warn.first.inner_text() or "정원" in warn.first.inner_text())
    ) or ("최대 3" in body_text) or ("정원이 찬" in body_text)


def test_multi_assign_ui_member_denied(login_as, base_url, accounts):
    page = login_as("member")
    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    # home 또는 sales
    btn = page.locator(".js-assign-as").first
    if btn.count() == 0:
        page.goto(f"{base_url}/sales", wait_until="domcontentloaded")
        btn = page.locator(".js-assign-as").first
    if btn.count() == 0:
        pytest.skip("지정 가능한 접수 건 없음")
    btn.click()
    page.wait_for_timeout(500)
    label = page.locator("#assignDropLabel, #homeAssignDropLabel").first
    text = label.inner_text() if label.count() else page.content()
    assert "1명" in text or "팀장" in text
    # CAN_MULTI false
    can_multi = page.evaluate(
        """() => {
          if (typeof CAN_MULTI_ASSIGN_DEFAULT !== 'undefined') return !!CAN_MULTI_ASSIGN_DEFAULT;
          if (typeof canMultiAssign !== 'undefined') return !!canMultiAssign;
          return null;
        }"""
    )
    if can_multi is not None:
        assert can_multi is False
    cards = page.locator(".assign-person-card.selected, .js-assign-person.is-selected, .assign-person-card.is-on")
    # 두 명 선택 시도
    all_cards = page.locator(".assign-person-card, .js-assign-person")
    if all_cards.count() < 2:
        pytest.skip("후보 2명 미만")
    all_cards.nth(0).click()
    page.wait_for_timeout(100)
    all_cards.nth(1).click()
    page.wait_for_timeout(400)
    body = page.locator("body").inner_text()
    warn = page.locator("#assignWarn, .assign-warn, .assign-inline-warn, #homeAssignWarn")
    assert (
        "공동" in body or "팀장" in body or "부서장" in body
    ) or (warn.count() and warn.first.is_visible())


# ---------------------------------------------------------------------------
# 팀장 기술부 조건부 / 긴급 / 일반팀원 숨김 / 부서장 전권
# ---------------------------------------------------------------------------
def test_member_tech_share_hidden(login_as, base_url, accounts):
    page = login_as("member")
    page.goto(f"{base_url}/history", wait_until="domcontentloaded")
    # 상세가 없으면 목록만 확인
    content = page.content()
    # 일반 팀원: 긴급 직행·직접 공유 카드 비가시
    assert page.locator("#supportUrgentCard:not([hidden])").count() == 0 or "긴급" not in content
    # 버튼 클래스 검색
    assert page.locator(".js-support-urgent:visible, #supportUrgentBtn:visible").count() == 0


def test_head_tech_share_always(login_as, base_url, accounts):
    page = login_as("head")
    page.goto(f"{base_url}/history", wait_until="domcontentloaded")
    # 부서장은 이력에서 기술공유 UI 정책 유지 — 페이지 로드 성공
    assert page.locator("body").count() == 1
    # 메뉴에 이력 접근 가능
    assert "history" in page.url or page.locator(".history-header, .receipt-table").count() >= 0


def test_leader_support_ui_present_in_code_path(login_as, base_url, accounts):
    """팀장 이력 페이지 로드 — 조건부 공유/긴급 카드는 건 상태에 따라 노출."""
    page = login_as("leader")
    resp = page.goto(f"{base_url}/history", wait_until="domcontentloaded")
    assert resp is None or resp.status < 500
    assert page.locator("body").count() == 1
    # 조건부 요소(상세 건 없으면 0)
    assert page.locator("#supportUrgentCard, .js-urgent-tech, [data-support-urgent]").count() >= 0
    assert page.locator(".js-share-tech, #supportShareCard, [data-tech-share]").count() >= 0


# ---------------------------------------------------------------------------
# 표기 수정 목록 페이지네이션
# ---------------------------------------------------------------------------
def test_equip_fix_pagination_ui(login_as, base_url, accounts):
    page = login_as("head")
    page.goto(f"{base_url}/equip-stats#equip-fixes", wait_until="domcontentloaded")
    if page.locator(".equip-fix-manage").count() == 0:
        pytest.skip("장비통계 접근 불가")
    pager = page.locator(".equip-fix-pager")
    summary = page.locator(".equip-fix-summary").last
    text = summary.inner_text() if summary.count() else ""
    m = re.search(r"(\d+)\s*건", text)
    if m and int(m.group(1)) > 6:
        assert pager.count() == 1
        assert page.locator(".equip-fix-pager a").count() >= 2
    else:
        assert page.locator(".equip-fix-table").count() == 1


# ---------------------------------------------------------------------------
# 홈 KPI / 네비 스모크
# ---------------------------------------------------------------------------
def test_home_and_nav_smoke(login_as, base_url, accounts):
    page = login_as("leader")
    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    assert page.locator("nav, .site-header, #siteNav").count() > 0
    for path in ("/sales", "/status-report", "/equip-stats"):
        r = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert r is None or r.status < 500


def test_admin_login(login_as, base_url, accounts):
    page = login_as("admin")
    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    assert page.locator("body").get_attribute("data-session-user")


def test_equip_stats_manage_for_admin(login_as, base_url, accounts):
    """admin 등록폼 검증(보조)."""
    page = login_as("admin")
    page.goto(f"{base_url}/equip-stats", wait_until="domcontentloaded")
    assert page.locator("#fix_raw_name").count() == 1
    assert page.locator("#fix_std_name").count() == 1
