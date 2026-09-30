# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path(__file__).resolve().parent / "users.db")
fixes = [
    ("jang_hb", 0, 1),
    ("lee_mj", 1, 0),
    ("sales_head", 1, 0),
]
for code, cr, ca in fixes:
    conn.execute(
        "UPDATE org_people SET can_receipt=?, can_as=? WHERE code=?",
        (cr, ca, code),
    )
conn.commit()
print(
    conn.execute(
        "SELECT code, can_receipt, can_as, team_code FROM org_people "
        "WHERE code IN ('jang_hb','lee_mj','sales_head')"
    ).fetchall()
)
