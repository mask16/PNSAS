# -*- coding: utf-8 -*-
"""제품군(웰딩 장비 / 용접봉) 구분·조직 시드·라벨 헬퍼."""

from __future__ import annotations

from flask import session

SESSION_WELDING = "welding"
SESSION_RODS = "rods"
PL_EQUIPMENT = "equipment"
PL_ROD = "rod"

RODS_AS_TEAM = "rods_as"
SALES_OFFICE_TEAM = "sales_office"
RODS_LAB_CODE = "rods_lab"
RODS_OFFICER_CODE = "rods_officer"
RODS_HANDLERS = ("rods_a", "rods_b", "rods_c", "rods_d")

SALES_OFFICES = [
    ("office_seoul", "서울사무소", 10),
    ("office_incheon", "인천사무소", 20),
    ("office_suwon", "수원사무소", 30),
    ("office_daejeon", "대전사무소", 40),
    ("office_gwangju", "광주사무소", 50),
    ("office_jeonju", "전주사무소", 60),
    ("office_daegu", "대구사무소", 70),
    ("office_busan", "부산사무소", 80),
    ("office_changwon", "창원사무소", 90),
    ("office_pohang", "포항사무소", 100),
    ("office_ulsan", "울산사무소", 110),
    ("office_ulsan_hi", "울산중공업사무소", 120),
    ("office_samho", "삼호사무소", 130),
]

SHARED_BOTH_CODES = {
    "quality_acc",
    "tech_mgmt_office",
}
SHARED_BOTH_CODES.update(code for code, _name, _ord in SALES_OFFICES)

RODS_ONLY_CODES = {
    RODS_OFFICER_CODE,
    RODS_LAB_CODE,
    *RODS_HANDLERS,
}

WELDING_HIDDEN_ON_RODS = {
    "tech_acc",
    "sales_head",
    "tech_group",
    "tech_lab",
}


def session_line() -> str:
    try:
        return session.get("product_line") or SESSION_WELDING
    except RuntimeError:
        return SESSION_WELDING


def is_rods() -> bool:
    return session_line() == SESSION_RODS


def receipt_line_code(line: str | None = None) -> str:
    value = line if line is not None else session_line()
    return PL_ROD if value == SESSION_RODS else PL_EQUIPMENT


def normalize_receipt_line(value) -> str:
    text = (value or "").strip().lower()
    if text in (PL_ROD, "rods", "rod", "용접봉"):
        return PL_ROD
    return PL_EQUIPMENT


def receipt_is_rod(receipt: dict | None) -> bool:
    if not receipt:
        return False
    return normalize_receipt_line(receipt.get("product_line")) == PL_ROD


def receipt_matches_session(receipt: dict | None) -> bool:
    if not receipt:
        return False
    return normalize_receipt_line(receipt.get("product_line")) == receipt_line_code()


def receipt_prefix(line: str | None = None) -> str:
    return "RD" if receipt_line_code(line) == PL_ROD else "HWDS"


def person_line(person: dict | None) -> str:
    if not person:
        return "equipment"
    return (person.get("product_line") or "equipment").strip() or "equipment"


def person_visible_on_line(person: dict | None, line: str | None = None) -> bool:
    if not person:
        return False
    code = person.get("id") or ""
    team = person.get("team_code") or ""
    pl = person_line(person)
    current = line if line is not None else session_line()
    if current == SESSION_RODS:
        if code in WELDING_HIDDEN_ON_RODS:
            return False
        if team in {"domestic", "affiliate", "export", "sales_head", "executive", "service"}:
            return False
        if pl == "rod" or pl == "both" or code in SHARED_BOTH_CODES or team in {
            SALES_OFFICE_TEAM,
            RODS_AS_TEAM,
            "tech_mgmt",
        }:
            if team == "field_support" and code == "tech_acc":
                return False
            if team == "field_support" and code == RODS_LAB_CODE:
                return True
            if team == "field_support" and code == "quality_acc":
                return True
            if team == "executive":
                return False
            return True
        return False
    if code in RODS_ONLY_CODES or team == RODS_AS_TEAM:
        return False
    if team == "field_support" and code == RODS_LAB_CODE:
        return False
    return True


def filter_login_groups(groups: list, line: str | None = None) -> list:
    current = line if line is not None else session_line()
    out = []
    for group in groups:
        team_code = group.get("team_code") or ""
        if team_code == "admin":
            out.append(group)
            continue
        members = []
        for member in group.get("members") or []:
            # login dropdown only has username/label; visibility applied upstream via people filter
            members.append(member)
        if members:
            out.append({**group, "members": members})
    return out


def status_options(line: str | None = None) -> list[tuple[str, str]]:
    if receipt_line_code(line) == PL_ROD:
        return [
            ("received", "접수"),
            ("in_progress", "진행중"),
            ("on_hold", "보류"),
            ("completed", "완료"),
        ]
    return [
        ("received", "접수"),
        ("in_progress", "진행중"),
        ("parts_waiting", "부품대기"),
        ("on_hold", "보류"),
        ("completed", "완료"),
    ]


def ui_labels(line: str | None = None) -> dict:
    if receipt_line_code(line) == PL_ROD:
        return {
            "product_field": "제품명",
            "product_placeholder": "예: S-7016",
            "spec_field": "규격",
            "lot_field": "로트번호",
            "serial_field": "제품 식별정보",
            "step3_title": "3. 점검 및 조치 내용",
            "repair_label": "조치내용",
            "step4_title": "4. 제품 교환",
            "part_name": "제품명",
            "part_qty": "수량(KG)",
            "parts_total": "제품 합계금액",
            "add_part": "+ 제품 행 추가",
            "support_tech": "기술연구소에 공유",
            "support_tech_long": "기술연구소 지원 요청 (기술적 난이도 높음)",
            "report_title": "용접봉 AS 조치보고서",
            "report_repair": "조치 내용",
            "report_parts": "제품 교환",
            "report_serial": "제품 식별정보",
            "receipt_hint": "형식: RD-YYMMDD-NNN (예: RD-260713-001)",
            "receipt_desc": "RD-YYMMDD-NNN",
        }
    return {
        "product_field": "장비명",
        "product_placeholder": "예: H600MR",
        "spec_field": "규격",
        "lot_field": "로트번호",
        "serial_field": "시리얼넘버",
        "step3_title": "3. 점검 및 수리 내용",
        "repair_label": "수리내용",
        "step4_title": "4. 교체 부품",
        "part_name": "부품명",
        "part_qty": "수량",
        "parts_total": "부품 합계금액",
        "add_part": "+ 부품 행 추가",
        "support_tech": "기술부에 공유",
        "support_tech_long": "기술부 지원 요청 (기술적 난이도 높음)",
        "report_title": "용접기 및 용접장비 수리보고서",
        "report_repair": "수리 내용",
        "report_parts": "교체 부품",
        "report_serial": "시리얼넘버",
        "receipt_hint": "형식: HWDS-YYMMDD-NNN (예: HWDS-260713-001)",
        "receipt_desc": "HWDS-YYMMDD-NNN",
    }


def product_identity_text(receipt: dict | None) -> str:
    if not receipt:
        return ""
    name = (receipt.get("equipment_name") or "").strip()
    spec = (receipt.get("spec") or "").strip()
    lot = (receipt.get("lot") or "").strip()
    parts = [p for p in (name, spec, lot) if p]
    return " / ".join(parts)


def ensure_schema_and_org(conn) -> None:
    team_cols = {row["name"] for row in conn.execute("PRAGMA table_info(teams)").fetchall()}
    people_cols = {row["name"] for row in conn.execute("PRAGMA table_info(org_people)").fetchall()}
    receipt_cols = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)").fetchall()}

    def _office_marked_removed(code):
        row = conn.execute(
            "SELECT 1 FROM app_meta WHERE key = ?",
            (f"office_removed:{code}",),
        ).fetchone()
        return bool(row)

    if "product_line" not in people_cols:
        conn.execute(
            "ALTER TABLE org_people ADD COLUMN product_line TEXT NOT NULL DEFAULT 'equipment'"
        )
    if "product_line" not in receipt_cols:
        conn.execute(
            "ALTER TABLE receipts ADD COLUMN product_line TEXT NOT NULL DEFAULT 'equipment'"
        )
    if "spec" not in receipt_cols:
        conn.execute("ALTER TABLE receipts ADD COLUMN spec TEXT NOT NULL DEFAULT ''")
    if "lot" not in receipt_cols:
        conn.execute("ALTER TABLE receipts ADD COLUMN lot TEXT NOT NULL DEFAULT ''")
    if "office" not in receipt_cols:
        conn.execute("ALTER TABLE receipts ADD COLUMN office TEXT NOT NULL DEFAULT ''")

    conn.execute(
        """
        INSERT OR IGNORE INTO teams (code, name, category, sort_order, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            SALES_OFFICE_TEAM,
            "지역 영업사무소",
            "domestic",
            5,
            "제품군 공용",
        ),
    )
    conn.execute(
        "UPDATE teams SET note = ? WHERE code = ? AND note LIKE ?",
        ("제품군 공용", SALES_OFFICE_TEAM, "%13개 사무소 공용%"),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO teams (code, name, category, sort_order, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            RODS_AS_TEAM,
            "AS담당관실",
            "service",
            58,
            "최고기술경영실 산하 · 용접봉 AS 처리",
        ),
    )

    existing_office_codes = {
        (row["code"] or "").casefold()
        for row in conn.execute(
            "SELECT code FROM org_people WHERE team_code = ?",
            (SALES_OFFICE_TEAM,),
        ).fetchall()
    }
    # 관리자가 삭제한 기본 사무소는 재기동 시 복구하지 않는다 (삭제 기록이 없으면 보정).
    if existing_office_codes:
        for code, _name, _sort_order in SALES_OFFICES:
            if code.casefold() not in existing_office_codes and not _office_marked_removed(code):
                conn.execute(
                    "INSERT OR IGNORE INTO app_meta (key, value) VALUES (?, ?)",
                    (f"office_removed:{code}", "1"),
                )

    for code, name, sort_order in SALES_OFFICES:
        if _office_marked_removed(code):
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO org_people
                (code, name, title, role_note, team_code, can_receipt, can_as, sort_order, product_line)
            VALUES (?, ?, '', '사무소 공용', ?, 1, 0, ?, 'both')
            """,
            (code, name, SALES_OFFICE_TEAM, sort_order),
        )
        conn.execute(
            "UPDATE org_people SET product_line='both', team_code=?, can_receipt=1, can_as=0 "
            "WHERE code=? COLLATE NOCASE",
            (SALES_OFFICE_TEAM, code),
        )

    rods_people = [
        (RODS_OFFICER_CODE, "AS담당관", "담당관", "접수·의뢰 겸용", RODS_AS_TEAM, 1, 1, 10),
        ("rods_a", "A담당자", "", "AS 처리", RODS_AS_TEAM, 0, 1, 20),
        ("rods_b", "B담당자", "", "AS 처리", RODS_AS_TEAM, 0, 1, 30),
        ("rods_c", "C담당자", "", "AS 처리", RODS_AS_TEAM, 0, 1, 40),
        ("rods_d", "D담당자", "", "AS 처리", RODS_AS_TEAM, 0, 1, 50),
        (RODS_LAB_CODE, "기술연구소 계정", "", "용접봉 지원요청", "field_support", 0, 0, 30),
    ]
    for row in rods_people:
        conn.execute(
            """
            INSERT OR IGNORE INTO org_people
                (code, name, title, role_note, team_code, can_receipt, can_as, sort_order, product_line)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'rod')
            """,
            row,
        )
        conn.execute(
            """
            UPDATE org_people
            SET name=?, title=?, role_note=?, team_code=?, can_receipt=?, can_as=?,
                sort_order=?, product_line='rod'
            WHERE code=? COLLATE NOCASE
            """,
            (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[0]),
        )

    conn.execute(
        "UPDATE org_people SET product_line='both' WHERE code IN ('quality_acc', 'tech_mgmt_office')"
    )
    conn.execute(
        "UPDATE org_people SET product_line='equipment' "
        "WHERE IFNULL(product_line,'') IN ('', 'equipment') "
        "AND code NOT IN ('quality_acc', 'tech_mgmt_office') "
        "AND team_code NOT IN (?, ?)"
        ,
        (SALES_OFFICE_TEAM, RODS_AS_TEAM),
    )
    _ = team_cols  # schema probe kept for future team columns
