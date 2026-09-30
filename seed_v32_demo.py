# -*- coding: utf-8 -*-
"""데모용 추가 접수 시드 (자동감지 시나리오 포함). 기존 데이터가 30건 미만일 때만 보충."""
from datetime import date, datetime, timedelta
import json
import sys

sys.path.insert(0, r"d:\Warehouse\wwwroot\pns as")
import app as m


def _mk(i, **kw):
    today = date.today()
    base = {
        "id": m._next_id + i,
        "receipt_no": f"HWDS-{(today - timedelta(days=kw.get('ago', 0))).strftime('%y%m%d')}-{100+i:03d}",
        "receipt_date": (today - timedelta(days=kw.get("ago", 0))).isoformat(),
        "team_id": kw.get("team_id", "domestic"),
        "team": m._team_name(kw.get("team_id", "domestic")),
        "customer_name": kw["customer"],
        "customer_contact": "",
        "equipment_name": kw["equip"],
        "symptoms": kw.get("symptoms", "데모 증상"),
        "as_staff": kw.get("as_staff", ""),
        "as_staff_name": kw.get("as_staff_name", ""),
        "receipt_staff": kw.get("receipt_staff", "choi_yh"),
        "receipt_staff_name": kw.get("receipt_staff_name", "최영호"),
        "status": kw.get("status", "received"),
        "charge_type": kw.get("charge_type", "free"),
        "billing_cost": kw.get("billing_cost"),
        "completed_date": kw.get("completed_date"),
        "created_at": datetime.now().isoformat(),
        "photos": [],
        "phone": "",
        "ship_date": kw.get("ship_date", ""),
        "support_quality": False,
        "support_tech": False,
        "created_by_user_id": None,
        "created_by_username": kw.get("receipt_staff_name", "최영호"),
        "severity": "medium",
        "escalation_level": kw.get("escalation_level", "normal"),
        "intervention_status": "none",
        "compensation_status": "none",
        "intervention_note": "",
        "compensation_note": "",
        "compensation_amount": "",
        "intervention_log": [],
        "intervention_by": "",
        "intervention_at": None,
        "billing_margin_rate": None,
        "billing_manual": 0,
        "billing_set_by": "",
    }
    return base


def main():
    if len(m.RECEIPTS) >= 28:
        print("enough receipts", len(m.RECEIPTS))
        return
    extras = []
    # 알앤티 H600MR 반복 3건
    for n, ago, st in [(1, 180, "completed"), (2, 90, "completed"), (3, 5, "in_progress")]:
        extras.append(
            _mk(
                len(extras),
                ago=ago,
                customer="알앤티",
                equip="H600MR",
                status=st,
                charge_type="paid" if n == 3 else "free",
                billing_cost=4380000 if n == 3 else None,
                completed_date=(date.today() - timedelta(days=ago - 2)).isoformat()
                if st == "completed"
                else None,
                as_staff="jang_hb",
                as_staff_name="장호빈",
            )
        )
    # 신성중공업 90일 내 3건
    for ago in (60, 30, 10):
        extras.append(
            _mk(
                len(extras),
                ago=ago,
                customer="신성중공업",
                equip=f"W-{ago}",
                status="received",
                team_id="affiliate",
            )
        )
    # 동양물산 다량부품 + 고액
    r = _mk(
        len(extras),
        ago=20,
        customer="동양물산",
        equip="Super S",
        status="completed",
        charge_type="paid",
        billing_cost=2690000,
        completed_date=(date.today() - timedelta(days=15)).isoformat(),
        as_staff="jang_hb",
        as_staff_name="장호빈",
    )
    extras.append(r)
    m.REPORT_DETAILS[r["receipt_no"]] = {
        "parts": [
            {"name": f"부품{i}", "qty": 1, "unit_price": 100000, "subtotal": 100000}
            for i in range(1, 6)
        ]
    }
    # 장기 미해결
    for cust, ago in [("태광후지킨", 20), ("현대미포조선", 25), ("삼호중공업", 18)]:
        extras.append(
            _mk(
                len(extras),
                ago=ago,
                customer=cust,
                equip="H600",
                status="parts_waiting",
                as_staff="jang_hb",
                as_staff_name="장호빈",
            )
        )
    # 수출 → 장호빈
    for i in range(2):
        extras.append(
            _mk(
                len(extras),
                ago=3 + i,
                customer=f"EXPORT-CUST-{i+1}",
                equip="ExportWelder",
                team_id="export",
                status="received",
                receipt_staff="kim_ys_ex",
                receipt_staff_name="김윤식",
                as_staff="jang_hb",
                as_staff_name="장호빈",
            )
        )

    for e in extras:
        m.RECEIPTS.append(e)
    m._next_id = max(int(x.get("id", 0)) for x in m.RECEIPTS) + 1
    m._save_service_data()
    print("seeded", len(extras), "total", len(m.RECEIPTS))


if __name__ == "__main__":
    main()
