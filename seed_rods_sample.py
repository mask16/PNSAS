# -*- coding: utf-8 -*-
"""용접봉 AS 샘플 접수 100건. 웰딩 장비 데이터는 유지하고, 이전 용접봉 샘플만 교체."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import app as m
import product_line_ext as plex

SEED_MARKER = "__rods_sample_seed__"
TARGET = 100
START = date(2026, 1, 6)
END = date(2026, 8, 5)

CUSTOMERS = [
    "현대중공업", "한화오션", "삼성중공업", "HD현대미포", "삼호중공업",
    "포스코", "현대제철", "세아베스틸", "동국제강", "고려아연",
    "두산에너빌리티", "효성중공업", "LS전선", "STX엔진", "대한조선",
    "대우조선해양서비스", "한국가스공사", "SK에너지", "GS칼텍스", "S-OIL",
]
PRODUCTS = [
    ("S-7016", "3.2mm"),
    ("S-7016", "4.0mm"),
    ("S-7018", "3.2mm"),
    ("S-7018", "4.0mm"),
    ("S-6013", "2.6mm"),
    ("S-6013", "3.2mm"),
    ("SC-70S", "1.2mm"),
    ("SC-80S", "1.2mm"),
    ("FC-71T", "1.4mm"),
    ("FCW-71T", "1.2mm"),
    ("S-309L", "3.2mm"),
    ("S-316L", "2.6mm"),
    ("S-308L", "3.2mm"),
    ("HYUNDAI S-16", "4.0mm"),
    ("S-6010", "4.0mm"),
]
SYMPTOMS = [
    "아크 불안정 및 스패터 과다",
    "슬래그 박리 불량",
    "비드 형상 불량",
    "기공 발생",
    "균열 의심",
    "용착 금속 강도 미달",
    "흡습으로 인한 용접 불량",
    "로트 혼입 의심",
    "피복 박리",
    "송급 불량(와이어)",
    "고객 클레임(외관 불량)",
    "보관 환경 이슈로 품질 저하",
]
EXCHANGE = [
    ("S-7016", 4200),
    ("S-7018", 4800),
    ("S-6013", 3100),
    ("SC-70S", 5500),
    ("FC-71T", 6100),
    ("S-309L", 9800),
    ("S-316L", 11200),
]
STATUSES = (
    ["received"] * 22
    + ["in_progress"] * 28
    + ["on_hold"] * 10
    + ["completed"] * 40
)
HANDLERS = [
    ("rods_a", "A담당자"),
    ("rods_b", "B담당자"),
    ("rods_c", "C담당자"),
    ("rods_d", "D담당자"),
]
OFFICES = plex.SALES_OFFICES
OFFICE_STAFF_NAMES = ["김민수", "이서연", "박지훈", "최유진", "정우성", "한소희", "오태양"]


def _purge_previous():
    removed = set()
    for no, detail in list(m.REPORT_DETAILS.items()):
        if isinstance(detail, dict) and detail.get("seed_tag") == SEED_MARKER:
            removed.add(no)
    keep = []
    for r in m.RECEIPTS:
        no = r.get("receipt_no") or ""
        if (
            r.get("created_by_username") == SEED_MARKER
            or no in removed
            or str(r.get("customer_contact") or "").startswith("[용접봉샘플]")
        ):
            removed.add(no)
            continue
        keep.append(r)
    m.RECEIPTS[:] = keep
    for no in list(m.REPORT_DETAILS):
        if no in removed:
            del m.REPORT_DETAILS[no]
    return len(removed)


def _spread_dates(n: int) -> list[date]:
    span = max((END - START).days, 1)
    dates = [START + timedelta(days=int(round(i * span / (n - 1)))) for i in range(n)]
    rng = random.Random(20260805)
    rng.shuffle(dates)
    return dates


def _build_detail(receipt, rng: random.Random, status: str, product: str, spec: str):
    parts = []
    if status in {"completed", "in_progress"} and rng.random() < 0.55:
        name, price = rng.choice(EXCHANGE)
        if rng.random() < 0.08:
            qty = rng.randint(1050, 2800)
        else:
            qty = rng.choice([20, 40, 80, 100, 200, 400, 500, 800])
        parts.append(
            {
                "name": name,
                "spec": spec,
                "qty": qty,
                "unit_price": price,
                "subtotal": int(qty * price),
            }
        )
    loc = rng.choice(["고객사 현장", "사무소 창고", "공장 회수", "기술연구소"])
    return {
        "report_date": receipt.get("completed_date") or receipt["receipt_date"],
        "phone": receipt.get("phone") or "",
        "serial_no": f"{product}/{spec}/{receipt.get('lot') or '-'}",
        "re_repair": "Y" if rng.random() < 0.1 else "N",
        "receipt_staff_name": receipt.get("receipt_staff_name") or "",
        "as_technician": receipt.get("as_staff_name") or "",
        "ship_date": receipt.get("ship_date") or "",
        "warranty": "",
        "inspection_date": receipt["receipt_date"],
        "affiliation": receipt.get("team") or "",
        "process_location": loc,
        "ship_out_date": receipt.get("completed_date") or "",
        "inspection_result": receipt["symptoms"] + " 확인",
        "repair_content": "교환 제품 출고 및 사용 조건 안내" if parts else "보관·사용 조건 점검 후 조치",
        "detailed_work": "용접봉 샘플 시드 조치 내용",
        "parts": parts,
        "support_history": (
            "기술연구소 지원 요청" if receipt.get("support_tech") else "지원 요청 이력 없음"
        ),
        "photos": [],
        "remarks": "용접봉 샘플 데이터",
        "seed_tag": SEED_MARKER,
        "as_status": status,
        "support_quality": receipt.get("support_quality"),
        "support_tech": receipt.get("support_tech"),
        "support_tech_mgmt": False,
    }


def main():
    removed = _purge_previous()
    team_id = plex.RODS_AS_TEAM
    team = m._team_name(team_id) or "AS담당관실"
    seq_by_day: dict[str, int] = defaultdict(int)
    for r in m.RECEIPTS:
        no = r.get("receipt_no") or ""
        parts = no.split("-")
        if len(parts) == 3 and parts[0] == "RD":
            try:
                seq_by_day[parts[1]] = max(seq_by_day[parts[1]], int(parts[2]))
            except ValueError:
                pass

    next_id = max((int(r.get("id") or 0) for r in m.RECEIPTS), default=0) + 1
    dates = _spread_dates(TARGET)
    rng = random.Random(100)
    created = 0
    office_count = 0

    for i, receipt_date in enumerate(dates):
        status = STATUSES[i % len(STATUSES)]
        product, spec = PRODUCTS[i % len(PRODUCTS)]
        if i % 17 == 0:
            product, spec = "S-7016", "4.0mm"
        customer = CUSTOMERS[i % len(CUSTOMERS)]
        symptom = SYMPTOMS[i % len(SYMPTOMS)]
        lot = f"L{receipt_date.strftime('%y%m')}{rng.randint(10, 99):02d}{chr(65 + (i % 6))}"
        day_key = receipt_date.strftime("%y%m%d")
        seq_by_day[day_key] += 1
        receipt_no = f"RD-{day_key}-{seq_by_day[day_key]:03d}"

        use_office = i % 5 == 0
        if use_office:
            office_code, office_name, _ord = OFFICES[i % len(OFFICES)]
            receipt_staff = ""
            receipt_staff_name = OFFICE_STAFF_NAMES[i % len(OFFICE_STAFF_NAMES)]
            office = office_name
            created_by_name = office_name
            office_count += 1
        else:
            receipt_staff = plex.RODS_OFFICER_CODE
            receipt_staff_name = "AS담당관"
            office = ""
            created_by_name = SEED_MARKER

        if status == "received" and i % 9 == 0:
            as_staff, as_staff_name = "", ""
        else:
            as_staff, as_staff_name = HANDLERS[i % len(HANDLERS)]

        charge = "paid" if i % 4 == 0 else "free"
        completed_date = None
        billing_cost = None
        if status == "completed":
            completed_date = (receipt_date + timedelta(days=rng.randint(1, 12))).isoformat()
            if completed_date > END.isoformat():
                completed_date = END.isoformat()
            if charge == "paid":
                billing_cost = str(rng.choice([180000, 360000, 720000, 1450000, 2680000]))

        item = {
            "id": next_id,
            "receipt_no": receipt_no,
            "receipt_date": receipt_date.isoformat(),
            "team_id": team_id,
            "team": team,
            "customer_name": customer,
            "customer_contact": f"[용접봉샘플] {customer} 품질담당",
            "equipment_name": product,
            "symptoms": symptom,
            "as_staff": as_staff,
            "as_staff_name": as_staff_name,
            "receipt_staff": receipt_staff,
            "receipt_staff_name": receipt_staff_name,
            "office": office,
            "status": status,
            "charge_type": charge,
            "billing_cost": billing_cost,
            "completed_date": completed_date,
            "created_at": datetime.combine(receipt_date, datetime.min.time().replace(hour=9)).isoformat(),
            "photos": [],
            "phone": f"010-{rng.randint(2000, 9999)}-{rng.randint(1000, 9999)}",
            "ship_date": (receipt_date - timedelta(days=rng.randint(20, 240))).isoformat(),
            "support_quality": bool(i % 21 == 0),
            "support_tech": bool(i % 13 == 0),
            "created_by_user_id": None,
            "created_by_username": created_by_name if use_office else SEED_MARKER,
            "severity": "medium",
            "escalation_level": "monitor" if i % 19 == 0 else "normal",
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
            "product_line": plex.PL_ROD,
            "spec": spec,
            "lot": lot,
        }
        m.RECEIPTS.append(item)
        m.REPORT_DETAILS[receipt_no] = _build_detail(item, rng, status, product, spec)
        next_id += 1
        created += 1

    m._next_id = next_id
    m._save_service_data()

    rods = [
        r
        for r in m.RECEIPTS
        if plex.normalize_receipt_line(r.get("product_line")) == plex.PL_ROD
    ]
    print(f"이전 용접봉 샘플 제거: {removed}건")
    print(f"신규 용접봉 샘플: {created}건 (사무소 접수 {office_count}건)")
    print(f"용접봉 전체: {len(rods)}건 / 전체 접수: {len(m.RECEIPTS)}건")
    print(f"기간: {START} ~ {END} / 번호 예: {rods[0]['receipt_no'] if rods else '-'}")


if __name__ == "__main__":
    main()
