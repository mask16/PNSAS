"""v3.2 AS 업무 규칙 — 순수 함수 (Flask/세션 비의존)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

# 자동 감지 임계값 (6.12)
AUTO_FLAG_RULES = {
    "repeat_equipment": 3,  # 동일 고객·장비 누적 접수
    "high_billing": 3_000_000,  # 청구비용
    "high_parts_total": 2_000_000,  # 부품합계
    "long_open_days": 14,  # 장기 미해결
    "many_parts_kinds": 4,  # 부품 종류
    "repeat_customer_days": 90,
    "repeat_customer_count": 3,
    "rod_mass_kg": 1000,
}

# 에스컬레이션 등급 (6.11) — 기존 severity와 병행 매핑
ESCALATION_OPTIONS = [
    ("normal", "일반"),
    ("monitor", "모니터링"),
    ("critical", "심각·개입중"),
    ("cleared", "문제없음(검토완료)"),
    ("closed", "종결"),
]
ESCALATION_RANK = {
    "normal": 0,
    "monitor": 1,
    "critical": 2,
    "cleared": 3,
    "closed": 4,
}

# 레거시 severity → escalation
SEVERITY_TO_ESCALATION = {
    "low": "normal",
    "medium": "normal",
    "high": "monitor",
    "critical": "critical",
}
ESCALATION_TO_SEVERITY = {
    "normal": "medium",
    "monitor": "high",
    "critical": "critical",
    "cleared": "low",
    "closed": "low",
}

COMPENSATION_V32 = [
    ("none", "해당없음"),
    ("reviewing", "검토중"),
    ("negotiating", "협의중"),
    ("agreed", "합의완료"),
    ("dispute", "분쟁·소송"),
]

AS_STATUS_OPTIONS = [
    ("in_progress", "진행중"),
    ("parts_waiting", "부품대기"),
    ("on_hold", "보류"),
    ("completed", "완료"),
]

PENDING_STATUSES = frozenset(
    {"received", "draft", "in_progress", "parts_waiting", "on_hold"}
)
WAIT_QUEUE_STATUSES = frozenset({"received", "draft"})
PROGRESS_QUEUE_STATUSES = frozenset({"in_progress", "parts_waiting", "on_hold"})
ACTIVE_AS_STATUSES = frozenset(
    {"in_progress", "parts_waiting", "on_hold", "completed"}
)

DEFAULT_MARGIN_RATE = 30.0


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_pending(status: str | None) -> bool:
    return (status or "") in PENDING_STATUSES


def is_wait_queue(status: str | None) -> bool:
    """미착수(접수·임시저장) — 대기 순번 풀."""
    return (status or "") in WAIT_QUEUE_STATUSES


def is_progress_queue(status: str | None) -> bool:
    """착수(진행중·부품대기·보류) — 처리중 순번 풀."""
    return (status or "") in PROGRESS_QUEUE_STATUSES


def in_team_queue_scope(receipt: dict, team_id: str | None) -> bool:
    """팀 대기열 집계 범위 — 수출·내수 미지정은 합산. 이관 후 handling_team 포함."""
    if not team_id:
        return True
    tid = receipt.get("team_id")
    hid = (receipt.get("handling_team_id") or tid or "").strip()
    if team_id in ("export", "domestic"):
        return tid in ("domestic", "export") or hid in ("domestic", "export")
    return tid == team_id or hid == team_id


def status_label(status: str | None) -> str:
    mapping = {
        "received": "접수",
        "draft": "임시저장",
        "in_progress": "진행중",
        "parts_waiting": "부품대기",
        "on_hold": "보류",
        "completed": "완료",
    }
    return mapping.get(status or "", status or "")


def warranty_verdict(ship_date: Any, inspect_date: Any = None) -> dict:
    """워런티 판정: 출하일 기준 점검일(또는 오늘)까지 365일 이내."""
    ship = parse_date(ship_date)
    if not ship:
        return {"code": "unknown", "label": "판단 불가", "days": None}
    inspect = parse_date(inspect_date) or date.today()
    days = (inspect - ship).days
    if days < 0:
        return {"code": "unknown", "label": "판단 불가", "days": days}
    if days <= 365:
        return {"code": "warranty", "label": "워런티 대상", "days": days}
    return {"code": "expired", "label": "워런티 만료", "days": days}


def suggest_re_repair(
    customer_name: str,
    equipment_name: str,
    serial_no: str,
    all_receipts: list,
    report_details: dict,
    exclude_receipt_no: str = "",
) -> dict:
    """동일 고객+장비(+시리얼) 과거 완료 이력 있으면 Y 제안."""
    cust = (customer_name or "").strip()
    equip = (equipment_name or "").strip()
    serial = (serial_no or "").strip()
    if not cust or not equip:
        return {"suggested": "N", "auto": False, "matched": []}

    matched = []
    for r in all_receipts:
        if r.get("receipt_no") == exclude_receipt_no:
            continue
        if r.get("status") != "completed":
            continue
        if (r.get("customer_name") or "").strip() != cust:
            continue
        if (r.get("equipment_name") or "").strip() != equip:
            continue
        detail = report_details.get(r["receipt_no"]) or {}
        other_serial = (detail.get("serial_no") or "").strip()
        if serial and other_serial and serial != other_serial:
            continue
        matched.append(r["receipt_no"])

    if matched:
        return {"suggested": "Y", "auto": True, "matched": matched}
    return {"suggested": "N", "auto": True, "matched": []}


def calc_parts_total(parts: list | None) -> int:
    total = 0
    for p in parts or []:
        try:
            total += int(p.get("subtotal") or 0)
        except (TypeError, ValueError):
            try:
                qty = int(p.get("qty") or 0)
                price = int(p.get("unit_price") or 0)
                total += qty * price
            except (TypeError, ValueError):
                pass
    return total


def calc_billing_from_margin(
    parts_total: int,
    margin_rate: float | None = None,
    manual_amount: int | None = None,
    manual_locked: bool = False,
) -> dict:
    """마진율 기반 청구비용. manual_locked면 직접 책정액 유지."""
    if manual_locked and manual_amount is not None:
        return {
            "parts_total": parts_total,
            "margin_rate": margin_rate if margin_rate is not None else DEFAULT_MARGIN_RATE,
            "billing_cost": int(manual_amount),
            "manual": True,
        }
    rate = DEFAULT_MARGIN_RATE if margin_rate is None else float(margin_rate)
    billing = int(round(parts_total * (1 + rate / 100.0))) if parts_total else 0
    return {
        "parts_total": parts_total,
        "margin_rate": rate,
        "billing_cost": billing,
        "manual": False,
    }


def past_history(
    customer_name: str,
    equipment_name: str,
    all_receipts: list,
    exclude_receipt_no: str = "",
) -> dict:
    """동일 고객사 과거이력 + 동일 장비 건 구분."""
    cust = (customer_name or "").strip()
    equip = (equipment_name or "").strip()
    same_customer = []
    same_equipment = []
    if not cust:
        return {"same_customer": [], "same_equipment": []}
    for r in sorted(all_receipts, key=lambda x: x.get("created_at") or "", reverse=True):
        if r.get("receipt_no") == exclude_receipt_no:
            continue
        if (r.get("customer_name") or "").strip() != cust:
            continue
        item = {
            "receipt_no": r.get("receipt_no"),
            "receipt_date": r.get("receipt_date"),
            "equipment_name": r.get("equipment_name"),
            "status": r.get("status"),
            "status_label": status_label(r.get("status")),
            "team": r.get("team"),
            "symptoms": (r.get("symptoms") or "")[:80],
        }
        same_customer.append(item)
        if equip and (r.get("equipment_name") or "").strip() == equip:
            same_equipment.append(item)
    return {
        "same_customer": same_customer[:20],
        "same_equipment": same_equipment[:20],
    }


def incomplete_first_key(receipt: dict) -> tuple:
    """미완료 건을 완료보다 위에."""
    pending = 0 if is_pending(receipt.get("status")) else 1
    return (pending, receipt.get("created_at") or "", receipt.get("receipt_no") or "")


def recent_board_sort_key(receipt: dict) -> tuple:
    """홈·영업접수 목록 정렬.

    1) 접수 → 접수일 과거순 (오래된 건 위 · 독촉)
    2) 진행중(임시·진행·부품대기·보류) → 접수일 과거순
    3) 완료 → 접수일 최신순
    """
    status = receipt.get("status") or ""
    rd = (receipt.get("receipt_date") or "")[:10]
    parsed = parse_date(rd)
    ordinal = parsed.toordinal() if parsed else 0
    no = receipt.get("receipt_no") or ""
    if status == "received":
        return (0, ordinal, no)
    if status == "completed":
        return (2, -ordinal, no)
    # draft / in_progress / parts_waiting / on_hold
    return (1, ordinal, no)


def count_queue_summary(
    receipts: list,
    *,
    team_id: str | None = None,
    person_id: str | None = None,
) -> dict:
    """팀·개인 대기/진행 건수 요약."""
    person_id = (person_id or "").strip()
    team_unassigned_wait = 0
    team_in_progress = 0
    assigned_to_me_wait = 0
    my_in_progress = 0

    for r in receipts:
        status = r.get("status") or ""
        as_staff = (r.get("as_staff") or "").strip()
        in_team = in_team_queue_scope(r, team_id)

        if in_team and not as_staff and is_wait_queue(status):
            team_unassigned_wait += 1
        if in_team and is_progress_queue(status):
            team_in_progress += 1
        if person_id and as_staff == person_id:
            if is_wait_queue(status):
                assigned_to_me_wait += 1
            if is_progress_queue(status):
                my_in_progress += 1

    return {
        "team_unassigned_wait": team_unassigned_wait,
        "team_in_progress": team_in_progress,
        "assigned_to_me_wait": assigned_to_me_wait,
        "my_in_progress": my_in_progress,
    }


def queue_info(receipt: dict, all_receipts: list) -> dict:
    """지정/미지정 — 접수(대기)·진행중 풀을 분리해 순번 계산."""
    status = receipt.get("status") or ""
    if status == "completed":
        return {"text": "완료됨", "type": "done", "main": "완료됨", "label": "", "rank": "", "scope": ""}

    is_wait = is_wait_queue(status)
    is_prog = is_progress_queue(status)
    if not is_wait and not is_prog:
        return {"text": "—", "type": "none", "main": "—", "label": "", "rank": "", "scope": ""}

    pool_statuses = WAIT_QUEUE_STATUSES if is_wait else PROGRESS_QUEUE_STATUSES
    label_prefix = "[대기] 대기" if is_wait else "[처리중] 처리중"
    queue_type = "waiting" if is_wait else "progress"

    as_staff = (receipt.get("as_staff") or "").strip()
    if as_staff:
        pool = [
            r
            for r in all_receipts
            if (r.get("as_staff") or "").strip() == as_staff
            and (r.get("status") or "") in pool_statuses
        ]
    else:
        team_id = receipt.get("team_id")
        if team_id in ("export", "domestic"):
            pool = [
                r
                for r in all_receipts
                if r.get("team_id") in ("domestic", "export")
                and (r.get("status") or "") in pool_statuses
                and not (r.get("as_staff") or "").strip()
            ]
        else:
            pool = [
                r
                for r in all_receipts
                if r.get("team_id") == team_id
                and (r.get("status") or "") in pool_statuses
                and not (r.get("as_staff") or "").strip()
            ]

    pool.sort(key=lambda x: x.get("created_at") or "")
    try:
        pos = next(i + 1 for i, r in enumerate(pool) if r.get("id") == receipt.get("id"))
    except StopIteration:
        return {"text": "—", "type": "none", "main": "—", "label": "", "rank": "", "scope": ""}

    if as_staff:
        scope = (receipt.get("as_staff_name") or as_staff).strip()
    elif receipt.get("team_id") in ("export", "domestic"):
        scope = "내수영업팀"
    else:
        scope = (receipt.get("team") or team_id or "AS 대응팀").strip()
        if scope.endswith(" 팀"):
            scope = scope[:-2].strip() or scope

    rank_text = f"{pos}/{len(pool)}번째"
    main = f"{label_prefix} {rank_text}"
    return {
        "text": f"{main} ({scope})" if scope else main,
        "main": main,
        "label": label_prefix,
        "rank": rank_text,
        "scope": scope,
        "type": queue_type,
    }


def normalize_escalation(receipt: dict) -> str:
    esc = (receipt.get("escalation_level") or "").strip()
    if esc in ESCALATION_RANK:
        return esc
    return SEVERITY_TO_ESCALATION.get(receipt.get("severity") or "medium", "normal")


def normalize_compensation(code: str | None) -> str:
    c = (code or "none").strip()
    # 레거시 매핑
    legacy = {
        "paid": "agreed",
        "rejected": "dispute",
        "negotiating": "negotiating",
        "agreed": "agreed",
        "none": "none",
        "reviewing": "reviewing",
        "dispute": "dispute",
    }
    return legacy.get(c, "none")


def build_auto_detect_indexes(
    all_receipts: list,
    today: date | None = None,
) -> dict:
    """자동감지용 사전집계 — O(n). auto_detect_result에 넘기면 반복 스캔을 피한다."""
    today = today or date.today()
    cutoff = today - timedelta(days=AUTO_FLAG_RULES["repeat_customer_days"])
    eq_counts: dict[tuple[str, str], int] = {}
    cust_recent: dict[str, int] = {}
    for r in all_receipts or []:
        cust = (r.get("customer_name") or "").strip()
        equip = (r.get("equipment_name") or "").strip()
        if cust and equip:
            key = (cust, equip)
            eq_counts[key] = eq_counts.get(key, 0) + 1
        if cust:
            rd = parse_date(r.get("receipt_date"))
            if rd and rd >= cutoff:
                cust_recent[cust] = cust_recent.get(cust, 0) + 1
    return {
        "today": today,
        "eq_counts": eq_counts,
        "cust_recent": cust_recent,
    }


def auto_detect_flags(
    receipt: dict,
    all_receipts: list,
    report_details: dict,
    today: date | None = None,
    indexes: dict | None = None,
) -> list[str]:
    """자동 감지 사유 코드 목록 (강제 에스컬레이션 없음)."""
    return auto_detect_result(
        receipt, all_receipts, report_details, today, indexes=indexes
    )["flags"]


def auto_detect_result(
    receipt: dict,
    all_receipts: list,
    report_details: dict,
    today: date | None = None,
    indexes: dict | None = None,
) -> dict:
    """자동 감지 결과 + 임계값·실측값 상세.

    반환: {flags, details, labels}
    - flags: 사유 코드 목록
    - details: 코드별 {count/value, threshold, ...}
    - labels: 화면 표시용 문구
    """
    flags: list[str] = []
    details: dict = {}
    if indexes:
        today = indexes.get("today") or today or date.today()
        eq_counts = indexes.get("eq_counts") or {}
        cust_recent = indexes.get("cust_recent") or {}
    else:
        today = today or date.today()
        eq_counts = None
        cust_recent = None

    cust = (receipt.get("customer_name") or "").strip()
    equip = (receipt.get("equipment_name") or "").strip()
    detail = report_details.get(receipt.get("receipt_no") or "") or {}
    rules = AUTO_FLAG_RULES

    # 1) 동일 장비 반복 접수 (고객사+장비 누적 ≥ 3)
    if cust and equip:
        if eq_counts is not None:
            same_eq = eq_counts.get((cust, equip), 0)
        else:
            same_eq = sum(
                1
                for r in all_receipts
                if (r.get("customer_name") or "").strip() == cust
                and (r.get("equipment_name") or "").strip() == equip
            )
        if same_eq >= rules["repeat_equipment"]:
            flags.append("repeat_equipment")
            details["repeat_equipment"] = {
                "count": same_eq,
                "threshold": rules["repeat_equipment"],
            }

    # 2) 고액 청구 (유상 · 청구 ≥ 300만 또는 부품합계 ≥ 200만)
    try:
        billing = int(receipt.get("billing_cost") or 0)
    except (TypeError, ValueError):
        billing = 0
    parts_total = calc_parts_total(detail.get("parts"))
    if receipt.get("charge_type") == "paid" and (
        billing >= rules["high_billing"] or parts_total >= rules["high_parts_total"]
    ):
        flags.append("high_billing")
        details["high_billing"] = {
            "billing_cost": billing,
            "parts_total": parts_total,
            "billing_threshold": rules["high_billing"],
            "parts_threshold": rules["high_parts_total"],
        }

    # 3) 장기 미해결 (접수 후 14일+ & 미완료)
    if is_pending(receipt.get("status")):
        rd = parse_date(receipt.get("receipt_date"))
        if rd:
            days = (today - rd).days
            if days >= rules["long_open_days"]:
                flags.append("long_open")
                details["long_open"] = {
                    "days": days,
                    "threshold": rules["long_open_days"],
                    "status": receipt.get("status"),
                }

    # 4) 부품 다량 교체 (4종 이상)
    parts = detail.get("parts") or []
    kinds = len([p for p in parts if (p.get("name") or "").strip()])
    if kinds >= rules["many_parts_kinds"]:
        flags.append("many_parts")
        details["many_parts"] = {
            "count": kinds,
            "threshold": rules["many_parts_kinds"],
        }

    # 5) 동일 고객사 최근 90일 내 3건+
    if cust:
        if cust_recent is not None:
            recent = cust_recent.get(cust, 0)
        else:
            cutoff = today - timedelta(days=rules["repeat_customer_days"])
            recent = 0
            for r in all_receipts:
                if (r.get("customer_name") or "").strip() != cust:
                    continue
                rd = parse_date(r.get("receipt_date"))
                if rd and rd >= cutoff:
                    recent += 1
        if recent >= rules["repeat_customer_count"]:
            flags.append("repeat_customer")
            details["repeat_customer"] = {
                "count": recent,
                "days": rules["repeat_customer_days"],
                "threshold": rules["repeat_customer_count"],
            }

    # 6) 용접봉: 교체 중량 1,000kg(1톤) 이상
    pl = (receipt.get("product_line") or "equipment").strip().lower()
    if pl in ("rod", "rods"):
        total_kg = 0.0
        for p in parts:
            try:
                total_kg += float(p.get("qty") or 0)
            except (TypeError, ValueError):
                pass
        if total_kg >= rules["rod_mass_kg"]:
            flags.append("rod_mass")
            details["rod_mass"] = {
                "kg": total_kg,
                "threshold": rules["rod_mass_kg"],
            }

    labels = [format_flag_label(code, details.get(code)) for code in flags]
    return {"flags": flags, "details": details, "labels": labels}


FLAG_LABELS = {
    "repeat_equipment": "동일장비 반복접수",
    "high_billing": "고액청구",
    "long_open": "장기미해결",
    "many_parts": "부품다량교체",
    "repeat_customer": "동일고객 집중접수",
    "rod_mass": "대량교체(1톤+)",
}


def format_flag_label(code: str, detail: dict | None = None) -> str:
    """임계값·실측을 포함한 표시 문구."""
    base = FLAG_LABELS.get(code, code)
    d = detail or {}
    if code == "repeat_equipment" and d.get("count") is not None:
        return f"{base} {d['count']}회(기준 {d.get('threshold')}회+)"
    if code == "high_billing":
        parts = []
        if d.get("billing_cost"):
            parts.append(f"청구 {int(d['billing_cost']):,}원")
        if d.get("parts_total"):
            parts.append(f"부품 {int(d['parts_total']):,}원")
        return f"{base}" + (f" ({', '.join(parts)})" if parts else "")
    if code == "long_open" and d.get("days") is not None:
        return f"{base} {d['days']}일(기준 {d.get('threshold')}일+)"
    if code == "many_parts" and d.get("count") is not None:
        return f"{base} {d['count']}종(기준 {d.get('threshold')}종+)"
    if code == "repeat_customer" and d.get("count") is not None:
        return (
            f"{base} {d.get('days')}일 내 {d['count']}건"
            f"(기준 {d.get('threshold')}건+)"
        )
    if code == "rod_mass" and d.get("kg") is not None:
        return f"{base} {d['kg']:.0f}kg(기준 {d.get('threshold')}kg+)"
    return base


def auto_detect_accuracy(candidates_reviewed: list) -> dict:
    """6.13 자동 감지 정확도 통계 (현재 데이터 기준 재계산).

    candidates_reviewed: 규칙에 한 번이라도 걸린 건들의 현재 escalation_level 목록.
    - total_flagged: 누적 자동감지 이력 (검토 여부 무관)
    - confirmed: 실제 문제로 확인 (모니터링·심각·종결)
    - false_positive: 오탐 (문제없음)
    - accuracy: confirmed / (confirmed + false_positive) × 100
      · 「일반」(미검토)은 분모에서 제외
      · 검토 0건이면 None → UI는 "—" 표시 (0%와 구분)
    """
    confirmed = 0
    false_pos = 0
    for level in candidates_reviewed:
        esc = level if isinstance(level, str) else normalize_escalation(level)
        if esc in ("monitor", "critical", "closed"):
            confirmed += 1
        elif esc == "cleared":
            false_pos += 1
        # normal 등 미검토는 분모·분자 모두 제외
    denom = confirmed + false_pos
    if denom == 0:
        rate = None
    else:
        rate = int(round(confirmed / denom * 100))
    return {
        "total_flagged": len(candidates_reviewed),
        "confirmed": confirmed,
        "false_positive": false_pos,
        "accuracy": rate,
        "unreviewed": sum(
            1
            for level in candidates_reviewed
            if (level if isinstance(level, str) else normalize_escalation(level))
            not in ("monitor", "critical", "closed", "cleared")
        ),
    }
