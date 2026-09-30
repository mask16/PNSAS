# -*- coding: utf-8 -*-
"""One-shot DB migration for v3.2 features."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "users.db"


def main():
    conn = sqlite3.connect(DB)
    # 부서 시드는 app.py _init_user_db가 담당. 삭제된 부서를 여기서 복구하지 않음.
    # (구버전 호환: sales_head 팀이 있을 때만 인원 권한 보정)
    has_sales_head = conn.execute(
        "SELECT 1 FROM teams WHERE code='sales_head' COLLATE NOCASE"
    ).fetchone()
    if has_sales_head:
        conn.execute(
            "UPDATE org_people SET team_code='sales_head', can_receipt=1, can_as=0 "
            "WHERE code='sales_head'"
        )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(receipts)")}
    for col, ddl in [
        ("escalation_level", "TEXT NOT NULL DEFAULT 'normal'"),
        ("compensation_amount", "TEXT NOT NULL DEFAULT ''"),
        ("intervention_log", "TEXT NOT NULL DEFAULT '[]'"),
        ("billing_margin_rate", "TEXT"),
        ("billing_manual", "INTEGER NOT NULL DEFAULT 0"),
        ("billing_set_by", "TEXT NOT NULL DEFAULT ''"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE receipts ADD COLUMN {col} {ddl}")
            print("added column", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_code TEXT NOT NULL DEFAULT '',
            team_code TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            receipt_no TEXT NOT NULL DEFAULT '',
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    # Map legacy severity to escalation where empty/default
    conn.execute(
        """
        UPDATE receipts SET escalation_level =
            CASE severity
                WHEN 'critical' THEN 'critical'
                WHEN 'high' THEN 'monitor'
                ELSE 'normal'
            END
        WHERE IFNULL(escalation_level, '') IN ('', 'normal')
          AND severity IN ('high', 'critical')
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT code, team_code, can_receipt, can_as FROM org_people WHERE code='sales_head'"
    ).fetchone()
    print("sales_head", row)
    print("done")


if __name__ == "__main__":
    main()
