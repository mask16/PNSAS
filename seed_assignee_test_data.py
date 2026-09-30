# -*- coding: utf-8 -*-
"""
부서 담당자별 테스트 접수 시드.
- 기간: 2025-01-01 ~ 2026-12-31
- 담당자(업무팀 소속, 접수/AS 가능)당 50건
- 재실행 시 이전 테스트 시드(__test_seed__)만 교체
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import app as m

SEED_MARKER = "__test_seed__"
PER_PERSON = 50
START = date(2025, 1, 1)
END = date(2026, 12, 31)

SKIP_TEAMS = {"executive", "tech_mgmt", "field_support", "sales_head"}

CUSTOMERS = [
    "알앤티", "대한조선", "삼성중공업", "현대중공업", "두산에너빌리티",
    "포스코퓨처엠", "한화오션", "현대건설기계", "대우조선해양", "STX엔진",
    "세아베스틸", "동국제강", "고려아연", "LS전선", "효성중공업",
]
EQUIPMENT = [
    "H600MR", "H350S", "W-400", "Super Arc", "H500i",
    "Plasma X2", "WireFeed Pro", "CoolMax 200", "Torch T-18", "Invert 300",
]
SYMPTOMS = [
    "아크 불안정", "냉각수 누수", "토치 접지 불량", "와이어 송급 이상",
    "전원 차단 반복", "과열 알람", "가스 유량 불량", "제어반 통신 오류",
    "팬 소음 과다", "용접 비드 불량",
]
PARTS_POOL = [
    ("토치 본체", 180000),
    ("접지 케이블", 45000),
    ("냉각수 호스", 85000),
    ("와이어 롤러", 32000),
    ("드라이브 모터", 320000),
    ("제어보드", 450000),
    ("노즐 세트", 28000),
    ("가스 레귤레이터", 95000),
]
STATUSES = [
    "received", "draft", "in_progress", "parts_waiting", "on_hold",
    "completed", "completed", "completed", "in_progress", "received",
]


def _team_name(code: str) -> str:
    try:
        return m._team_name(code) or code
    except Exception:
        return code


def _target_people():
    people = []
    for p in m._get_org_people():
        team = (p.get("team_code") or "").strip()
        if not team or team in SKIP_TEAMS:
            continue
        if not (p.get("can_receipt") or p.get("can_as")):
            continue
        people.append(p)
    return people


def _purge_previous_seed():
    """이전 테스트 시드 제거. 마커/비고/시드태그로 식별."""
    removed_nos = set()
    for no, detail in list(m.REPORT_DETAILS.items()):
        if not isinstance(detail, dict):
            continue
        if (
            detail.get("seed_tag") == SEED_MARKER
            or detail.get("remarks") == "담당자별 테스트 시드"
        ):
            removed_nos.add(no)

    keep = []
    for r in m.RECEIPTS:
        no = r.get("receipt_no")
        if (
            r.get("created_by_username") == SEED_MARKER
            or no in removed_nos
            or str(r.get("customer_contact") or "").startswith("[테스트시드]")
        ):
            removed_nos.add(no)
            continue
        keep.append(r)

    m.RECEIPTS[:] = keep
    for no in list(m.REPORT_DETAILS.keys()):
        if no in removed_nos:
            del m.REPORT_DETAILS[no]
    return len(removed_nos)


def _date_key(d: date) -> str:
    return d.strftime("%y%m%d")


def _spread_dates(n: int) -> list[date]:
    if n <= 1:
        return [START]
    span = (END - START).days
    return [START + timedelta(days=int(round(i * span / (n - 1)))) for i in range(n)]


def _build_detail(receipt, rng: random.Random, status: str):
    ship = receipt["ship_date"]
    inspect = receipt.get("inspection_date") or receipt["receipt_date"]
    parts = []
    if status == "completed" or rng.random() < 0.35:
        for _ in range(rng.randint(0, 3)):
            name, price = rng.choice(PARTS_POOL)
            qty = rng.randint(1, 2)
            parts.append(
                {
                    "name": name,
                    "qty": qty,
                    "unit_price": price,
                    "subtotal": qty * price,
                }
            )
    re_repair = "Y" if rng.random() < 0.12 else "N"
    loc = "현장" if rng.random() < 0.6 else "공장"
    return {
        "report_date": receipt.get("completed_date") or receipt["receipt_date"],
        "phone": receipt.get("phone") or "",
        "serial_no": f"SN-{rng.randint(2020, 2025)}-{rng.randint(10000, 99999)}",
        "re_repair": re_repair,
        "receipt_staff_name": receipt.get("receipt_staff_name") or "",
        "as_technician": receipt.get("as_staff_name") or "",
        "ship_date": ship,
        "warranty": "",
        "inspection_date": inspect,
        "affiliation": receipt.get("team") or "",
        "process_location": loc,
        "ship_out_date": receipt.get("completed_date") or "",
        "inspection_result": rng.choice(SYMPTOMS) + " 점검",
        "repair_content": "부품 교체 및 동작 점검" if parts else "조정·청소 후 정상 확인",
        "detailed_work": "테스트 시드 작업 내용",
        "parts": parts,
        "support_history": "지원 요청 이력 없음",
        "photos": [],
        "remarks": "담당자별 테스트 시드",
        "seed_tag": SEED_MARKER,
        "as_status": status,
    }


def main():
    people = _target_people()
    if not people:
        print("대상 담당자가 없습니다. (업무팀 + 접수/AS 권한)")
        return

    removed = _purge_previous_seed()
    print(f"이전 테스트 시드 제거: {removed}건")
    print(f"대상 담당자: {len(people)}명 × {PER_PERSON}건")

    # 기존 접수번호 일자별 순번
    seq_by_day: dict[str, int] = defaultdict(int)
    for r in m.RECEIPTS:
        no = r.get("receipt_no") or ""
        parts = no.split("-")
        if len(parts) == 3 and parts[0] == "HWDS":
            try:
                seq_by_day[parts[1]] = max(seq_by_day[parts[1]], int(parts[2]))
            except ValueError:
                pass

    next_id = max((int(r.get("id") or 0) for r in m.RECEIPTS), default=0) + 1
    dates = _spread_dates(PER_PERSON)
    created = 0

    for person in people:
        rng = random.Random(f"seed-{person['id']}")
        team_id = person["team_code"]
        team = person.get("team") or _team_name(team_id)
        display = person.get("display_name") or person["name"]

        for i, receipt_date in enumerate(dates):
            status = STATUSES[i % len(STATUSES)]
            charge = "paid" if (i % 3 == 0) else "free"
            customer = CUSTOMERS[(i + hash(person["id"])) % len(CUSTOMERS)]
            equip = EQUIPMENT[(i * 3 + len(person["id"])) % len(EQUIPMENT)]
            symptom = SYMPTOMS[i % len(SYMPTOMS)]

            day_key = _date_key(receipt_date)
            seq_by_day[day_key] += 1
            receipt_no = f"HWDS-{day_key}-{seq_by_day[day_key]:03d}"

            # 접수/AS 담당 배정
            if person.get("can_receipt") and person.get("can_as"):
                # 겸용: 홀수는 접수담당, 짝수는 AS담당 중심 + 상호 지정
                receipt_staff = person["id"]
                receipt_staff_name = display
                as_staff = person["id"]
                as_staff_name = display
            elif person.get("can_receipt"):
                receipt_staff = person["id"]
                receipt_staff_name = display
                # 같은 팀 AS 담당자 찾기
                peers = [p for p in people if p["team_code"] == team_id and p.get("can_as")]
                peer = peers[i % len(peers)] if peers else None
                as_staff = peer["id"] if peer else ""
                as_staff_name = (peer.get("display_name") or peer["name"]) if peer else ""
            else:
                as_staff = person["id"]
                as_staff_name = display
                peers = [p for p in people if p["team_code"] == team_id and p.get("can_receipt")]
                peer = peers[i % len(peers)] if peers else None
                receipt_staff = peer["id"] if peer else person["id"]
                receipt_staff_name = (
                    (peer.get("display_name") or peer["name"]) if peer else display
                )

            completed_date = None
            billing_cost = None
            if status == "completed":
                completed_date = (receipt_date + timedelta(days=rng.randint(1, 10))).isoformat()
                if completed_date > END.isoformat():
                    completed_date = END.isoformat()
                if charge == "paid":
                    billing_cost = str(rng.choice([150000, 280000, 450000, 890000, 1250000, 2300000]))

            ship_date = (receipt_date - timedelta(days=rng.randint(30, 700))).isoformat()
            inspect_date = receipt_date.isoformat()

            item = {
                "id": next_id,
                "receipt_no": receipt_no,
                "receipt_date": receipt_date.isoformat(),
                "team_id": team_id,
                "team": team,
                "customer_name": customer,
                "customer_contact": f"[테스트시드] {customer} 담당",
                "equipment_name": equip,
                "symptoms": symptom,
                "as_staff": as_staff,
                "as_staff_name": as_staff_name,
                "receipt_staff": receipt_staff,
                "receipt_staff_name": receipt_staff_name,
                "status": status,
                "charge_type": charge,
                "billing_cost": billing_cost,
                "completed_date": completed_date,
                "created_at": datetime.combine(receipt_date, datetime.min.time()).isoformat(),
                "photos": [],
                "phone": f"010-{rng.randint(1000,9999)}-{rng.randint(1000,9999)}",
                "ship_date": ship_date,
                "support_quality": bool(i % 17 == 0),
                "support_tech": bool(i % 19 == 0),
                "created_by_user_id": None,
                "created_by_username": SEED_MARKER,
                "severity": "medium",
                "escalation_level": "monitor" if i % 23 == 0 else "normal",
                "intervention_status": "none",
                "compensation_status": "none",
                "intervention_note": "",
                "compensation_note": "",
                "compensation_amount": "",
                "intervention_log": [],
                "intervention_by": "",
                "intervention_at": None,
                "billing_margin_rate": "30" if charge == "paid" else None,
                "billing_manual": 0,
                "billing_set_by": receipt_staff_name if charge == "paid" and billing_cost else "",
                "inspection_date": inspect_date,
            }
            m.RECEIPTS.append(item)
            m.REPORT_DETAILS[receipt_no] = _build_detail(item, rng, status)
            next_id += 1
            created += 1

        print(f"  - {team} / {display}: {PER_PERSON}건")

    m._next_id = next_id
    m._save_service_data()

    # 저장 검증: 마커가 유지되는지 확인
    tagged = sum(
        1
        for r in m.RECEIPTS
        if r.get("created_by_username") == SEED_MARKER
    )
    print(f"완료: 총 {created}건 등록 (전체 접수 {len(m.RECEIPTS)}건, 시드마커 {tagged}건)")
    print(f"기간: {START} ~ {END}")
    if tagged != created:
        print("경고: created_by_username 마커 수가 기대와 다릅니다. 서버 재시작 후 확인하세요.")


if __name__ == "__main__":
    main()
