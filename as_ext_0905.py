# -*- coding: utf-8 -*-
"""2026-09-05 AS 확장: 팀 이관·공동담당·지원피드백·장비통계·담당자별 실적.

install(app_module)로 헬퍼·라우트를 주입한다.
접수 부가정보는 REPORT_DETAILS[receipt_no] 아래에 저장한다
(_handling / _as_co / support_feedback / transfer_pending_id).
team_transfers·equip_name_fixes는 users.db SQL 테이블에 보관한다.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from flask import (
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import v32_rules as v32

TEAM_LEAD_MAP = {
    "domestic": "choi_yh",
    "affiliate": "kim_jh_aff",
    "export": "kim_ys_ex",
}
EQUIP_FIX_PAGE_SIZE = 6
AS_STAFF_MAX = 3
SUPPORT_SLA_DAYS = 2  # 지원요청 지연·팀장 기술공유 기준(달력일)

TEAM_LABELS = {
    "domestic": "내수영업팀",
    "affiliate": "관계사영업팀",
    "export": "수출영업팀",
}

_EXTRAS_KEYS = (
    "handling_team_id",
    "origin_team_tag",
    "as_staff_ids",
    "as_staff_names",
    "support_feedback",
    "transfer_pending_id",
    "transfer_pending_to",
)

_FEEDBACK_BLANK = {
    "status": "waiting",
    "shared_at": "",
    "confirmed_at": "",
    "replied_at": "",
    "reply": "",
    "urgent": False,
    "reopen_note": "",
}


def install(app_module):
    """app.py 모듈에 0905 헬퍼·라우트를 주입한다."""
    if getattr(app_module, "_as_ext_0905_installed", False):
        return True
    app = app_module.app

    # ------------------------------------------------------------------ DB
    def _ensure_0905_tables():
        with app_module._user_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS equip_name_fixes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_name TEXT NOT NULL,
                    standard_name TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_no TEXT NOT NULL,
                    from_team TEXT NOT NULL,
                    to_team TEXT NOT NULL,
                    proposed_as_staff TEXT NOT NULL DEFAULT '',
                    proposed_as_name TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_by TEXT NOT NULL DEFAULT '',
                    requested_by_name TEXT NOT NULL DEFAULT '',
                    requested_at TEXT NOT NULL,
                    decided_by TEXT NOT NULL DEFAULT '',
                    decided_at TEXT NOT NULL DEFAULT '',
                    decision_note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(receipts)").fetchall()
            }
            if "extras_json" not in cols:
                try:
                    conn.execute(
                        "ALTER TABLE receipts ADD COLUMN extras_json TEXT NOT NULL DEFAULT '{}'"
                    )
                except Exception:
                    pass

    _ensure_0905_tables()

    # ---------------------------------------------------------- time helpers
    def _now_iso():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _today_iso():
        return date.today().isoformat()

    def _person_code(person):
        if not person:
            return ""
        return (person.get("id") or person.get("code") or "").strip()

    def _team_label(code):
        code = (code or "").strip()
        if code in TEAM_LABELS:
            return TEAM_LABELS[code]
        try:
            for t in app_module._get_teams():
                if t["id"] == code:
                    return t["name"]
        except Exception:
            pass
        return code or "-"

    def _org_name(code):
        code = (code or "").strip()
        if not code:
            return ""
        try:
            for p in app_module._get_org_people():
                if p.get("id") == code:
                    return p.get("display_name") or p.get("name") or code
        except Exception:
            pass
        try:
            return app_module._staff_name("as_staff", code) or code
        except Exception:
            return code

    # ---------------------------------------------------- receipt extras
    def _detail_bucket(receipt_no):
        return app_module.REPORT_DETAILS.setdefault(receipt_no or "", {})

    def _receipt_extras(receipt):
        """접수 부가정보 딕셔너리 (REPORT_DETAILS 기반)."""
        if not receipt:
            return {}
        no = receipt.get("receipt_no") or ""
        detail = app_module.REPORT_DETAILS.get(no) or {}
        handling = detail.get("_handling") or {}
        co = detail.get("_as_co") or {}
        out = {
            "handling_team_id": handling.get("team_id")
            or receipt.get("handling_team_id")
            or "",
            "origin_team_tag": handling.get("origin_tag")
            or receipt.get("origin_team_tag")
            or "",
            "as_staff_ids": list(co.get("ids") or receipt.get("as_staff_ids") or []),
            "as_staff_names": list(
                co.get("names") or receipt.get("as_staff_names") or []
            ),
            "support_feedback": detail.get("support_feedback")
            or receipt.get("support_feedback")
            or {},
            "transfer_pending_id": detail.get("transfer_pending_id")
            or receipt.get("transfer_pending_id"),
            "transfer_pending_to": detail.get("transfer_pending_to")
            or receipt.get("transfer_pending_to")
            or "",
        }
        if not out["as_staff_ids"] and receipt.get("as_staff"):
            out["as_staff_ids"] = [receipt["as_staff"]]
            out["as_staff_names"] = [receipt.get("as_staff_name") or ""]
        if not out["handling_team_id"]:
            out["handling_team_id"] = receipt.get("team_id") or ""
        return out

    def _set_receipt_extras(receipt, **kwargs):
        if not receipt:
            return
        no = receipt.get("receipt_no") or ""
        if not no:
            return
        detail = _detail_bucket(no)
        if "handling_team_id" in kwargs or "origin_team_tag" in kwargs:
            handling = dict(detail.get("_handling") or {})
            if "handling_team_id" in kwargs:
                handling["team_id"] = kwargs["handling_team_id"] or ""
                receipt["handling_team_id"] = handling["team_id"]
            if "origin_team_tag" in kwargs:
                handling["origin_tag"] = kwargs["origin_team_tag"] or ""
                receipt["origin_team_tag"] = handling["origin_tag"]
            detail["_handling"] = handling
        if "as_staff_ids" in kwargs or "as_staff_names" in kwargs:
            co = dict(detail.get("_as_co") or {})
            if "as_staff_ids" in kwargs:
                co["ids"] = list(kwargs["as_staff_ids"] or [])
                receipt["as_staff_ids"] = co["ids"]
            if "as_staff_names" in kwargs:
                co["names"] = list(kwargs["as_staff_names"] or [])
                receipt["as_staff_names"] = co["names"]
            detail["_as_co"] = co
        if "support_feedback" in kwargs:
            detail["support_feedback"] = kwargs["support_feedback"] or {}
            receipt["support_feedback"] = detail["support_feedback"]
        if "transfer_pending_id" in kwargs:
            detail["transfer_pending_id"] = kwargs["transfer_pending_id"]
            receipt["transfer_pending_id"] = kwargs["transfer_pending_id"]
        if "transfer_pending_to" in kwargs:
            detail["transfer_pending_to"] = kwargs["transfer_pending_to"] or ""
            receipt["transfer_pending_to"] = detail["transfer_pending_to"]

    def _normalize_as_staff_fields(receipt):
        """as_staff / as_staff_name 을 as_staff_ids[0]·이름 조인과 동기화."""
        if not receipt:
            return
        extras = _receipt_extras(receipt)
        ids = [c for c in (extras.get("as_staff_ids") or []) if c]
        names = list(extras.get("as_staff_names") or [])
        while len(names) < len(ids):
            names.append(_org_name(ids[len(names)]))
        names = names[: len(ids)]
        if ids:
            receipt["as_staff"] = ids[0]
            receipt["as_staff_name"] = " · ".join(n for n in names if n) or _org_name(
                ids[0]
            )
        _set_receipt_extras(receipt, as_staff_ids=ids, as_staff_names=names)

    def _as_staff_codes(receipt):
        extras = _receipt_extras(receipt)
        ids = [c for c in (extras.get("as_staff_ids") or []) if c]
        if ids:
            return ids
        one = (receipt.get("as_staff") or "").strip()
        return [one] if one else []

    def _set_as_staff_list(receipt, codes, person=None):
        """공동 담당 최대 AS_STAFF_MAX명 설정. 1번이 주담당."""
        if not receipt:
            return
        cleaned = []
        seen = set()
        for c in codes or []:
            c = (c or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            cleaned.append(c)
            if len(cleaned) >= AS_STAFF_MAX:
                break
        names = [_org_name(c) for c in cleaned]
        _set_receipt_extras(receipt, as_staff_ids=cleaned, as_staff_names=names)
        if cleaned:
            receipt["as_staff"] = cleaned[0]
            receipt["as_staff_name"] = " · ".join(names)
        else:
            receipt["as_staff"] = ""
            receipt["as_staff_name"] = ""

    def _sync_as_staff_list_primary(receipt, code, name):
        """IA/A 저장 시 주담당을 목록 맨 앞으로 맞춤."""
        code = (code or "").strip()
        if not receipt or not code:
            return
        ids = _as_staff_codes(receipt)
        names = list(_receipt_extras(receipt).get("as_staff_names") or [])
        name = (name or "").strip() or _org_name(code)
        if code in ids:
            idx = ids.index(code)
            ids = [code] + [c for i, c in enumerate(ids) if i != idx]
            if idx < len(names):
                nm = names[idx]
                names = [nm] + [n for i, n in enumerate(names) if i != idx]
            else:
                names = [name] + names
        else:
            ids = [code] + ids
            names = [name] + names
        ids = ids[:AS_STAFF_MAX]
        names = names[: len(ids)]
        _set_as_staff_list(receipt, ids)

    def _handling_team_id(receipt):
        extras = _receipt_extras(receipt)
        return (extras.get("handling_team_id") or receipt.get("team_id") or "").strip()

    def _can_multi_assign_as(receipt, person):
        """공동 담당(2명+) 지정: 부서장/총괄/대표/admin 또는 현재 담당팀 팀장."""
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        if not person:
            return False
        try:
            if app_module._is_sales_head(person):
                return True
        except Exception:
            pass
        try:
            if app_module._is_exec_ops_privileged(person):
                return True
        except Exception:
            pass
        code = _person_code(person)
        team = _handling_team_id(receipt) if receipt else (person.get("team_code") or "")
        if team and TEAM_LEAD_MAP.get(team) == code:
            return True
        # 이관 후 담당팀 팀장(맵 외 타이틀·용접봉 AS담당관 등)
        person_team = (person.get("team_code") or "").strip()
        if team and person_team == team:
            try:
                if app_module._is_sales_team_leader(person):
                    return True
            except Exception:
                pass
        return False

    MULTI_ASSIGN_DENIED_MSG = (
        "담당자 여러 명 공동 지정은 팀장 또는 부서장·총괄·대표이사만 할 수 있습니다"
    )
    AS_STAFF_FULL_MSG = f"공동 담당은 최대 {AS_STAFF_MAX}명까지입니다. 정원이 찬 상태에서는 추가할 수 없습니다."

    def _validate_as_staff_assign_codes(receipt, person, codes):
        """지정·변경 저장 전 인원·권한 검증. 통과 시 None, 실패 시 오류 메시지."""
        codes = list(codes or [])
        if len(codes) > AS_STAFF_MAX:
            return AS_STAFF_FULL_MSG
        can_multi = _can_multi_assign_as(receipt, person)
        old_codes = _as_staff_codes(receipt)
        old_set = set(old_codes)
        if can_multi:
            return None
        # 일반 팀원: 결과 1명 이하(최초 지정·교체·해제) OK.
        # 기존 다수 지정은 유지·해제만 가능(신규 추가·인원 증가 불가).
        # 주의: 0→1은 허용해야 함(len 증가만으로 공동지정 거절하면 안 됨).
        if len(codes) > 1 and len(codes) > len(old_codes):
            return MULTI_ASSIGN_DENIED_MSG
        if len(codes) > 1 and any(c not in old_set for c in codes):
            return MULTI_ASSIGN_DENIED_MSG
        if len(old_codes) <= 1 and len(codes) > 1:
            return MULTI_ASSIGN_DENIED_MSG
        return None

    def _is_team_lead(person):
        if not person:
            return False
        code = _person_code(person)
        return code in TEAM_LEAD_MAP.values()

    def _can_access_equip_stats(person=None):
        """정직원 팀장 이상: 팀장·부서장/총괄/대표·admin. 사무소·지원부서·기술경영실 제외."""
        if person is None:
            person = app_module._get_person_for_session()
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        if not person:
            return False
        try:
            if app_module._is_office_person(person):
                return False
        except Exception:
            pass
        team = (person.get("team_code") or "").strip()
        if team in ("field_support", "tech_mgmt"):
            return False
        try:
            if app_module._is_sales_head(person):
                return True
        except Exception:
            pass
        # 일반 경영진(조회 전용) 제외 — 총괄·대표는 _is_sales_head에서 True
        if team == "executive":
            return False
        try:
            if app_module._is_sales_team_leader(person):
                return True
        except Exception:
            pass
        return _is_team_lead(person)

    def _can_manage_equip_fixes(person=None):
        """표기 수정 등록/삭제·유사후보: 부서장급·admin만."""
        if person is None:
            person = app_module._get_person_for_session()
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        if not person:
            return False
        try:
            return bool(app_module._is_sales_head(person))
        except Exception:
            return False

    # ---------------------------------------------------- support feedback
    def _ensure_support_feedback(
        receipt, kind, *, urgent=False, reopen_note="", requested_by_name=""
    ):
        """지원 공유 시 feedback 엔트리 초기화(waiting)."""
        if not receipt or kind not in ("quality", "tech"):
            return
        no = receipt.get("receipt_no") or ""
        detail = _detail_bucket(no)
        fb = dict(detail.get("support_feedback") or {})
        entry = dict(fb.get(kind) or {})
        if not entry or entry.get("status") in ("", None):
            entry = dict(_FEEDBACK_BLANK)
            entry["shared_at"] = _now_iso()
        elif entry.get("status") in ("replied", "rejected"):
            # 재요청 시 대기 재개
            entry["status"] = "waiting"
            entry["shared_at"] = _now_iso()
            entry["confirmed_at"] = ""
            entry["replied_at"] = ""
            entry["reply"] = ""
            entry["urgent"] = False
        if urgent:
            entry["urgent"] = True
        if reopen_note:
            entry["reopen_note"] = reopen_note
        if requested_by_name:
            entry["requested_by_name"] = requested_by_name
        elif not entry.get("requested_by_name"):
            try:
                p = app_module._get_person_for_session()
                entry["requested_by_name"] = (p or {}).get("name") or ""
            except Exception:
                pass
        fb[kind] = entry
        detail["support_feedback"] = fb
        receipt["support_feedback"] = fb

    def _auto_confirm_support_on_view(receipt, person):
        """품질/기술 담당자가 이력 불러오기 시 waiting → confirmed + 요청자 알림."""
        if not receipt or not person:
            return False
        kind = ""
        try:
            if app_module._is_quality_support_person(person):
                kind = "quality"
            elif app_module._is_tech_support_person(person):
                kind = "tech"
        except Exception:
            return False
        if not kind:
            return False
        flag = f"support_{kind}"
        if not (receipt.get(flag) or (app_module.REPORT_DETAILS.get(receipt.get("receipt_no") or "") or {}).get(flag)):
            return False
        no = receipt.get("receipt_no") or ""
        detail = _detail_bucket(no)
        fb = dict(detail.get("support_feedback") or {})
        entry = dict(fb.get(kind) or dict(_FEEDBACK_BLANK))
        if not entry.get("shared_at") and (receipt.get(flag) or detail.get(flag)):
            entry["shared_at"] = _now_iso()
        if entry.get("status") not in ("", "waiting"):
            return False
        if entry.get("status") == "waiting" or not entry.get("status"):
            entry["status"] = "confirmed"
            entry["confirmed_at"] = _now_iso()
            fb[kind] = entry
            detail["support_feedback"] = fb
            receipt["support_feedback"] = fb
            label = "품질팀" if kind == "quality" else app_module._support_tech_dept_label(receipt)
            for ac in _as_staff_codes(receipt)[:1]:
                try:
                    app_module._push_notification(
                        user_code=ac,
                        title=f"[지원확인] {no}",
                        body=f"{label}에서 확인했습니다.",
                        link="/history",
                        receipt_no=no,
                    )
                except Exception:
                    pass
            return True
        return False

    def _feedback_days_open(entry):
        shared = (entry or {}).get("shared_at") or ""
        if not shared:
            return 0
        try:
            dt = datetime.strptime(shared[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(shared[:10], "%Y-%m-%d")
            except ValueError:
                return 0
        # 달력일 기준
        return max(0, (datetime.now().date() - dt.date()).days)

    def _feedback_is_overdue(entry):
        status = (entry or {}).get("status") or "waiting"
        if status not in ("waiting", "confirmed"):
            return False
        return _feedback_days_open(entry) >= SUPPORT_SLA_DAYS

    def _support_status_label(status):
        return {
            "waiting": "대기중",
            "confirmed": "확인함",
            "replied": "회신완료",
            "rejected": "반려됨",
        }.get(status or "", status or "")

    def _support_open_items():
        """미종결(대기중·확인함) 지원요청 — 회신완료·반려됨 제외."""
        items = []
        for r in app_module._line_receipts():
            no = r.get("receipt_no") or ""
            detail = app_module.REPORT_DETAILS.get(no) or {}
            fb = detail.get("support_feedback") or {}
            for kind in ("quality", "tech"):
                if kind == "quality" and not (
                    r.get("support_quality") or detail.get("support_quality")
                ):
                    continue
                if kind == "tech" and not (
                    r.get("support_tech") or detail.get("support_tech")
                ):
                    continue
                entry = fb.get(kind) or {}
                status = entry.get("status") or "waiting"
                if status in ("", "replied", "rejected"):
                    continue
                days = _feedback_days_open(entry) if entry else 0
                overdue = _feedback_is_overdue(entry) if entry else False
                requester = (
                    entry.get("requested_by_name")
                    or r.get("as_staff_name")
                    or r.get("receipt_staff_name")
                    or ""
                )
                items.append(
                    {
                        "receipt_no": no,
                        "customer_name": r.get("customer_name") or "",
                        "equipment_name": r.get("equipment_name") or "",
                        "team_id": r.get("team_id") or "",
                        "team_name": _team_label(r.get("team_id")),
                        "kind": kind,
                        "kind_label": "품질팀"
                        if kind == "quality"
                        else app_module._support_tech_dept_label(r),
                        "status": status,
                        "status_label": _support_status_label(status),
                        "shared_at": entry.get("shared_at") or "",
                        "days_open": days,
                        "overdue": overdue,
                        "urgent": bool(entry.get("urgent")),
                        "reply": entry.get("reply") or "",
                        "reopen_note": entry.get("reopen_note") or "",
                        "requester": requester,
                        "as_status": r.get("status") or "",
                        "as_status_label": v32.status_label(r.get("status")),
                    }
                )
        items.sort(
            key=lambda x: (0 if x["overdue"] or x["urgent"] else 1, -x["days_open"])
        )
        return items

    # ---------------------------------------------------- team transfer
    def _pending_transfer_row(receipt_no):
        with app_module._user_db() as conn:
            row = conn.execute(
                """
                SELECT * FROM team_transfers
                WHERE receipt_no = ? AND status = 'pending'
                ORDER BY id DESC LIMIT 1
                """,
                (receipt_no,),
            ).fetchone()
        return dict(row) if row else None

    def _pending_transfers_for_person(person):
        """승인 대기 이관: 대상팀 팀장·부서장/총괄/대표/admin. 수신팀 구성원은 조회만."""
        if not person:
            return []
        code = _person_code(person)
        is_head = False
        try:
            is_head = bool(
                app_module._is_sales_head(person)
                or getattr(app_module, "_is_exec_ops_privileged", lambda p: False)(person)
                or session.get("user_role") == "admin"
            )
        except Exception:
            try:
                is_head = session.get("user_role") == "admin"
            except Exception:
                is_head = False
        lead_teams = [t for t, lead in TEAM_LEAD_MAP.items() if lead == code]
        viewer_team = (person.get("team_code") or "").strip()
        can_view_team = bool(
            viewer_team in TEAM_LEAD_MAP
            and (person.get("can_as") or person.get("can_receipt"))
        )
        with app_module._user_db() as conn:
            rows = conn.execute(
                "SELECT * FROM team_transfers WHERE status = 'pending' ORDER BY id DESC"
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            to_team = item.get("to_team") or ""
            can_decide = bool(is_head or to_team in lead_teams)
            can_see = bool(can_decide or (can_view_team and to_team == viewer_team))
            if not can_see:
                continue
            item["from_team_label"] = _team_label(item.get("from_team"))
            item["to_team_label"] = _team_label(to_team)
            item["can_decide"] = can_decide
            receipt = app_module._get_receipt_by_no(item.get("receipt_no") or "")
            if receipt:
                item["customer_name"] = receipt.get("customer_name") or ""
                item["equipment_name"] = receipt.get("equipment_name") or ""
            else:
                item["customer_name"] = ""
                item["equipment_name"] = ""
            out.append(item)
        return out

    def _transfer_as_candidates(team_code):
        """이관 대상 팀 AS담당(접수+AS/AS전담) 후보. 팀장에 · 팀장 표기."""
        team = (team_code or "").strip()
        if team not in TEAM_LEAD_MAP:
            return []
        lead = TEAM_LEAD_MAP.get(team) or ""
        people = []
        for p in app_module._get_org_people():
            if (p.get("team_code") or "") != team:
                continue
            if not p.get("can_as"):
                continue
            try:
                if hasattr(app_module, "plex") and not app_module.plex.person_visible_on_line(p):
                    continue
            except Exception:
                pass
            pid = p.get("id") or ""
            name = (p.get("name") or p.get("display_name") or pid).strip()
            is_lead = pid == lead
            label = f"{name} · 팀장" if is_lead and "팀장" not in name else name
            people.append(
                {
                    "id": pid,
                    "label": label,
                    "name": name,
                    "team_code": team,
                    "is_lead": is_lead,
                }
            )
        people.sort(key=lambda x: (0 if x.get("is_lead") else 1, x.get("name") or ""))
        return people

    def _can_decide_transfer(item, person):
        if not item or not person:
            return False
        code = _person_code(person)
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        try:
            if app_module._is_sales_head(person):
                return True
        except Exception:
            pass
        try:
            if getattr(app_module, "_is_exec_ops_privileged", lambda p: False)(person):
                return True
        except Exception:
            pass
        return TEAM_LEAD_MAP.get(item.get("to_team")) == code

    def _transfer_blocked_reason(receipt):
        if not receipt:
            return "접수 건을 찾을 수 없습니다."
        if app_module._is_receipt_completed(receipt):
            return "완료된 건은 이관할 수 없습니다."
        try:
            if app_module._is_rods():
                return "용접봉 라인에서는 팀 이관을 사용할 수 없습니다."
        except Exception:
            pass
        no = receipt.get("receipt_no") or ""
        extras = _receipt_extras(receipt)
        if extras.get("transfer_pending_id") or _pending_transfer_row(no):
            return "이미 대기 중인 이관 요청이 있습니다."
        return ""

    def _notify_transfer_team(to_team, receipt_no, from_team, reason):
        """대상 팀 전체(팀 코드 알림) + 팀장 개인 알림."""
        title = f"[이관요청] {receipt_no}"
        body = (
            f"{_team_label(from_team)} → {_team_label(to_team)}"
            + (f" / {reason[:80]}" if reason else "")
        )
        try:
            app_module._push_notification(
                user_code="",
                team_code=to_team,
                title=title,
                body=body or "타 팀 이관 요청이 도착했습니다.",
                link="/home",
                receipt_no=receipt_no,
            )
        except Exception:
            pass
        lead = TEAM_LEAD_MAP.get(to_team)
        if lead:
            try:
                app_module._push_notification(
                    user_code=lead,
                    title=title,
                    body=body or "타 팀 이관 요청이 도착했습니다.",
                    link="/home",
                    receipt_no=receipt_no,
                )
            except Exception:
                pass

    def _clear_service_technician(receipt):
        """이관 승인 시 서비스담당자(실제 저장 이력 표시) 초기화."""
        if not receipt:
            return
        no = receipt.get("receipt_no") or ""
        detail = app_module.REPORT_DETAILS.get(no)
        if detail is not None:
            detail["as_technician"] = ""

    # ---------------------------------------------------- equip stats
    def _levenshtein(a, b):
        a, b = a or "", b or ""
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                ins = cur[j - 1] + 1
                delete = prev[j] + 1
                sub = prev[j - 1] + (0 if ca == cb else 1)
                cur.append(min(ins, delete, sub))
            prev = cur
        return prev[-1]

    def _equip_norm_key(name):
        # 대소문자·공백만 다른 표기는 동일 키로 합침 (장비통계 안내문과 동일)
        text = re.sub(r"\s+", "", (name or "").strip())
        return text.casefold()

    def _load_equip_fixes_map():
        with app_module._user_db() as conn:
            rows = conn.execute(
                "SELECT raw_name, standard_name FROM equip_name_fixes ORDER BY id"
            ).fetchall()
        return {_equip_norm_key(r["raw_name"]): (r["standard_name"] or "").strip() for r in rows}

    def _apply_equip_fix(name, fixes_map=None):
        raw = (name or "").strip()
        if not raw:
            return raw
        fmap = fixes_map if fixes_map is not None else _load_equip_fixes_map()
        std = fmap.get(_equip_norm_key(raw))
        return std if std else raw

    def _digit_stem(name):
        """숫자 줄기 (예: H600MS → H600)."""
        m = re.match(r"^(.*?\d+)", (name or "").strip(), re.I)
        return m.group(1).casefold() if m else ""

    def _equip_candidate_excluded(a, b):
        """유사명 후보 제외 규칙."""
        a, b = (a or "").strip(), (b or "").strip()
        if not a or not b or a.casefold() == b.casefold():
            return True
        # 숫자 차이
        da = re.findall(r"\d+", a)
        db = re.findall(r"\d+", b)
        if da != db:
            return True
        # 한쪽만 -P 접미
        a_p = bool(re.search(r"-P\b", a, re.I)) or a.upper().endswith("-P")
        b_p = bool(re.search(r"-P\b", b, re.I)) or b.upper().endswith("-P")
        if a_p != b_p:
            base_a = re.sub(r"-P\b", "", a, flags=re.I).rstrip("-")
            base_b = re.sub(r"-P\b", "", b, flags=re.I).rstrip("-")
            if base_a.casefold() == base_b.casefold():
                return True
        # 숫자 줄기 뒤 1~2글자 접미사만 다름 (H600MS / H600MG)
        stem_a, stem_b = _digit_stem(a), _digit_stem(b)
        if stem_a and stem_a == stem_b:
            suf_a = a[len(stem_a) :] if a.lower().startswith(stem_a) else ""
            # use casefold lengths carefully
            la = a.casefold()
            lb = b.casefold()
            if la.startswith(stem_a) and lb.startswith(stem_b):
                suf_a = la[len(stem_a) :]
                suf_b = lb[len(stem_b) :]
                if 1 <= len(suf_a) <= 2 and 1 <= len(suf_b) <= 2 and suf_a != suf_b:
                    return True
        return False

    def _equip_series_base(name):
        """버전 비교용 계열 키 (통합형·개선판·-P 제거). Hi400i 계열 통합."""
        text = re.sub(r"\s+", " ", (name or "").strip())
        if re.match(r"^Hi\s*400i", text, re.I):
            return "hi400i"
        text = re.sub(r"\s*(통합형|개선판)\s*", " ", text, flags=re.I)
        text = re.sub(r"-P\b", "", text, flags=re.I).rstrip("- ").strip()
        return _equip_norm_key(text)

    def _equip_version_label(name):
        """버전 비교용 표시명."""
        text = re.sub(r"\s+", " ", (name or "").strip())
        pulse = bool(re.search(r"-P\b", text, re.I) or text.upper().endswith("-P"))
        if pulse:
            text = re.sub(r"-P\b", "", text, flags=re.I).rstrip("- ").strip()
        # Hi400i / Hi400iE / Hi400iD
        m = re.match(r"^Hi\s*400i\s*([ED])?\s*$", text, re.I)
        if m:
            suf = (m.group(1) or "").upper()
            if suf == "E":
                label = "이더넷통신형"
            elif suf == "D":
                label = "디바이스넷통신형"
            else:
                label = "기본형(패널)"
            return f"{label} (펄스)" if pulse else label
        # 통합형/개선판 태그 유지 표시
        tags = []
        if re.search(r"통합형", (name or ""), re.I):
            tags.append("통합형")
        if re.search(r"개선판", (name or ""), re.I):
            tags.append("개선판")
        base = re.sub(r"\s*(통합형|개선판)\s*", " ", text, flags=re.I)
        base = re.sub(r"\s+", " ", base).strip() or text
        if pulse:
            tags.append("펄스")
        if tags:
            return f"{base} ({', '.join(tags)})"
        return base

    def _pcb_part_hint(part_name):
        n = (part_name or "").casefold()
        if any(k in n for k in ("pcb", "피시비", "보드", "board", "기판")):
            return "PCB류"
        return ""

    def _build_equip_groups():
        fixes = _load_equip_fixes_map()
        groups = defaultdict(list)
        for r in app_module._line_receipts():
            raw = (r.get("equipment_name") or "").strip()
            if not raw:
                continue
            applied = _apply_equip_fix(raw, fixes)
            key = _equip_norm_key(applied)
            groups[key].append((raw, applied, r))
        result = []
        for key, items in groups.items():
            spellings = Counter(applied for _, applied, _ in items)
            display = spellings.most_common(1)[0][0]
            receipts = [r for _, _, r in items]
            paid = sum(1 for r in receipts if r.get("charge_type") == "paid")
            free = sum(1 for r in receipts if r.get("charge_type") != "paid")
            re_cnt = 0
            war_cnt = 0
            part_counter = Counter()
            for r in receipts:
                no = r.get("receipt_no") or ""
                detail = app_module.REPORT_DETAILS.get(no) or {}
                re_repair = detail.get("re_repair") or ""
                if re_repair in ("Y", "1회", "예"):
                    re_cnt += 1
                ship = detail.get("ship_date") or r.get("ship_date") or ""
                inspect = detail.get("inspection_date") or ""
                if v32.warranty_verdict(ship, inspect or None).get("code") == "warranty":
                    war_cnt += 1
                for p in detail.get("parts") or []:
                    pname = (p.get("name") or "").strip()
                    qty = p.get("qty") or p.get("quantity") or 1
                    try:
                        qty = int(qty)
                    except (TypeError, ValueError):
                        qty = 1
                    if pname:
                        part_counter[pname] += max(1, qty)
            total = len(receipts)
            re_rate = round((re_cnt / total) * 100, 1) if total else 0.0
            war_rate = round((war_cnt / total) * 100, 1) if total else 0.0
            paid_rate = round((paid / total) * 100, 1) if total else 0.0
            top = part_counter.most_common(1)
            top_part = top[0][0] if top else "-"
            top_part_count = top[0][1] if top else 0
            result.append(
                {
                    "key": key,
                    "name": display,
                    "series_base": _equip_series_base(display),
                    "total": total,
                    "paid": paid,
                    "free": free,
                    "paid_rate": paid_rate,
                    "re_repair_count": re_cnt,
                    "re_repair_rate": re_rate,
                    "warranty_count": war_cnt,
                    "warranty_rate": war_rate,
                    "top_part": top_part,
                    "top_part_count": top_part_count,
                    "highlight": re_rate >= 15.0,
                    "receipts": receipts,
                    "part_counter": part_counter,
                }
            )
        result.sort(key=lambda x: (-x["total"], x["name"].casefold()))
        return result

    def _equip_similar_candidates(groups=None):
        groups = groups or _build_equip_groups()
        by_key = {g["key"]: g for g in groups}
        names = [(g["key"], g["name"], g["total"]) for g in groups]
        pairs = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                ka, na, ca = names[i]
                kb, nb, cb = names[j]
                if _equip_candidate_excluded(na, nb):
                    continue
                dist = _levenshtein(ka, kb)
                if 1 <= dist <= 2:
                    pairs.append(
                        {
                            "a": na,
                            "b": nb,
                            "a_count": ca,
                            "b_count": cb,
                            "distance": dist,
                            "a_key": ka,
                            "b_key": kb,
                        }
                    )
        pairs.sort(key=lambda x: (x["distance"], x["a"].casefold()))
        return pairs

    def _equip_detail_payload(name, groups=None):
        groups = groups if groups is not None else _build_equip_groups()
        target = None
        want = _equip_norm_key(_apply_equip_fix(name))
        for g in groups:
            if g["key"] == want or _equip_norm_key(g["name"]) == _equip_norm_key(name):
                target = g
                break
        if not target:
            return None
        ver_counter = Counter()
        month_counter = Counter()
        cases = []
        for r in target["receipts"]:
            raw = (r.get("equipment_name") or "").strip()
            ver_counter[_equip_version_label(raw)] += 1
            mk = (r.get("receipt_date") or "")[:7]
            if mk:
                month_counter[mk] += 1
            no = r.get("receipt_no") or ""
            detail = app_module.REPORT_DETAILS.get(no) or {}
            cases.append(
                {
                    "receipt_no": no,
                    "receipt_date": r.get("receipt_date") or "",
                    "customer_name": r.get("customer_name") or "",
                    "team": _team_label(r.get("team_id")),
                    "status": r.get("status") or "",
                    "status_label": v32.status_label(r.get("status")),
                    "charge_type": r.get("charge_type") or "",
                    "as_staff_name": r.get("as_staff_name") or "",
                    "re_repair": detail.get("re_repair") or "",
                }
            )
        cases.sort(key=lambda x: x["receipt_date"] or "", reverse=True)
        today = date.today()
        months = []
        for i in range(11, -1, -1):
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            key = f"{y:04d}-{m:02d}"
            months.append({"key": key, "label": f"{m}월", "count": month_counter.get(key, 0)})
        max_bar = max([m["count"] for m in months] + [1])
        for m in months:
            m["pct"] = int(round(m["count"] / max_bar * 100))
        versions = [{"name": n, "count": c} for n, c in ver_counter.most_common()]
        top_parts = []
        pcb_total = 0
        for n, c in target["part_counter"].most_common(10):
            hint = _pcb_part_hint(n)
            if hint:
                pcb_total += c
            top_parts.append({"name": n, "count": c, "hint": hint})
        # 계열 버전 비교 (랭킹은 합치지 않고 상세에서만)
        series = target.get("series_base") or ""
        version_compare = []
        if series:
            siblings = [g for g in groups if g.get("series_base") == series]
            if len(siblings) >= 2 or series == "hi400i":
                for g in siblings:
                    version_compare.append(
                        {
                            "name": g["name"],
                            "label": _equip_version_label(g["name"]),
                            "total": g["total"],
                            "paid_rate": g["paid_rate"],
                            "re_repair_rate": g["re_repair_rate"],
                            "warranty_rate": g["warranty_rate"],
                            "highlight": g["highlight"],
                            "is_current": g["key"] == target["key"],
                        }
                    )
        return {
            "summary": {
                "name": target["name"],
                "total": target["total"],
                "paid": target["paid"],
                "free": target["free"],
                "paid_rate": target["paid_rate"],
                "re_repair_rate": target["re_repair_rate"],
                "warranty_rate": target["warranty_rate"],
                "top_part": target["top_part"],
                "highlight": target["highlight"],
            },
            "versions": versions,
            "version_compare": version_compare,
            "months": months,
            "top_parts": top_parts,
            "pcb_parts_total": pcb_total,
            "cases": cases[:15],
        }

    def _list_equip_fixes(page=1, q=""):
        q = (q or "").strip()
        q_cf = q.casefold()
        with app_module._user_db() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM equip_name_fixes ORDER BY id DESC"
                ).fetchall()
            ]
        if q_cf:
            rows = [
                r
                for r in rows
                if q_cf in (r.get("raw_name") or "").casefold()
                or q_cf in (r.get("standard_name") or "").casefold()
            ]
        total = len(rows)
        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError):
            page = 1
        size = EQUIP_FIX_PAGE_SIZE
        pages = max(1, (total + size - 1) // size) if total else 1
        if page > pages:
            page = pages
        start = (page - 1) * size
        end = min(start + size, total)
        page_rows = rows[start:end]
        if total == 0:
            summary = "전체 0건"
        else:
            summary = f"전체 {total}건 중 {start + 1}-{end}건 표시 ({page}/{pages} 페이지)"
        return {
            "rows": page_rows,
            "page": page,
            "pages": pages if total > size else (pages if total else 1),
            "show_pager": total > size,
            "total": total,
            "page_size": size,
            "q": q,
            "summary": summary,
            "page_numbers": list(range(1, pages + 1)) if total > size else [],
        }

    # ------------------------------------------------ staff performance
    def _blank_staff_row():
        return {
            "code": "",
            "name": "",
            "team_code": "",
            "team_name": "",
            "received": 0,
            "completed": 0,
            "other_team_support": 0,
        }

    def _build_staff_performance(year, period, period_keys, period_labels=None):
        """담당자별 실적 — 기간 탭 구간별로 분류.

        집계 기준(7-3, 팀 실적과 분리 유지):
        - 팀 전체 실적: 원 접수팀 기준 1건=1회 (이 함수에서 집계하지 않음)
        - 담당자별 실적: 공동담당·실제 처리자 각각 +1 (한 건에 3명이면 개인합 3)

        반환: (segments, totals, flat_rows)
        """
        people = {p["id"]: p for p in app_module._get_org_people()}
        ordered_keys = list(period_keys) if not isinstance(period_keys, set) else []
        if isinstance(period_keys, set):
            # 호출부가 set만 넘기면 overall period_rows 순서로 재구성 불가 → 정렬
            ordered_keys = sorted(period_keys)
        labels = period_labels or {}

        seg_counters = {
            k: defaultdict(_blank_staff_row) for k in ordered_keys
        }

        def bump(counters, code, name, field, receipt_team=""):
            code = (code or "").strip()
            if not code and not name:
                return
            key = code or f"name:{(name or '').strip()}"
            row = counters[key]
            if not row["code"] and not row["name"]:
                row["code"] = code
                person = people.get(code)
                row["name"] = (
                    (person or {}).get("display_name")
                    or (person or {}).get("name")
                    or name
                    or code
                    or "미지정"
                )
                row["team_code"] = (person or {}).get("team_code") or ""
                row["team_name"] = (
                    _team_label(row["team_code"]) if row["team_code"] else ""
                )
            row[field] += 1
            if (
                field == "received"
                and row["team_code"]
                and receipt_team
                and row["team_code"] != receipt_team
            ):
                row["other_team_support"] += 1

        def bump_receipt(counters, r, field):
            codes = _as_staff_codes(r)
            names = list(_receipt_extras(r).get("as_staff_names") or [])
            receipt_team = r.get("team_id") or ""
            if codes:
                for i, c in enumerate(codes):
                    nm = names[i] if i < len(names) else ""
                    bump(counters, c, nm, field, receipt_team)
            elif r.get("as_staff") or r.get("as_staff_name"):
                bump(
                    counters,
                    r.get("as_staff"),
                    r.get("as_staff_name"),
                    field,
                    receipt_team,
                )
            if field == "completed":
                no = r.get("receipt_no") or ""
                detail = app_module.REPORT_DETAILS.get(no) or {}
                pb = (detail.get("processed_by") or "").strip()
                pb_code = (detail.get("processed_by_code") or "").strip()
                if pb or pb_code:
                    if pb_code and pb_code not in codes:
                        bump(counters, pb_code, pb, "completed", receipt_team)
                    elif pb and not pb_code:
                        if not any(
                            (n or "").strip().casefold() == pb.casefold() for n in names
                        ):
                            bump(counters, "", pb, "completed", receipt_team)

        key_set = set(ordered_keys)
        for r in app_module._line_receipts():
            recv_key = app_module._period_bucket_key(
                r.get("receipt_date"), period, year
            )
            done_key = app_module._period_bucket_key(
                r.get("completed_date"), period, year
            )
            if recv_key in key_set:
                bump_receipt(seg_counters[recv_key], r, "received")
            if done_key in key_set and r.get("status") == "completed":
                bump_receipt(seg_counters[done_key], r, "completed")

        segments = []
        flat_map = defaultdict(_blank_staff_row)
        for k in ordered_keys:
            rows = sorted(
                (dict(v) for v in seg_counters[k].values() if v["received"] or v["completed"]),
                key=lambda x: (-x["received"], -x["completed"], (x["name"] or "").casefold()),
            )
            empty = not rows
            segments.append(
                {
                    "key": k,
                    "label": labels.get(k) or k,
                    "rows": rows,
                    "empty": empty,
                }
            )
            for row in rows:
                fk = row["code"] or f"name:{row['name']}"
                agg = flat_map[fk]
                if not agg["code"] and not agg["name"]:
                    agg.update(
                        {
                            "code": row["code"],
                            "name": row["name"],
                            "team_code": row["team_code"],
                            "team_name": row["team_name"],
                        }
                    )
                agg["received"] += row["received"]
                agg["completed"] += row["completed"]
                agg["other_team_support"] += row["other_team_support"]

        flat_rows = sorted(
            flat_map.values(),
            key=lambda x: (-x["received"], -x["completed"], (x["name"] or "").casefold()),
        )
        totals = {
            "name": "합계",
            "code": "",
            "team_name": "",
            "received": sum(r["received"] for r in flat_rows),
            "completed": sum(r["completed"] for r in flat_rows),
            "other_team_support": sum(r["other_team_support"] for r in flat_rows),
            "is_total": True,
        }
        return segments, totals, flat_rows

    def _can_view_staff_performance(person=None):
        """장비별 AS통계와 동일: 팀장·부서장/총괄·대표·admin."""
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        if person is None:
            try:
                person = app_module._get_person_for_session()
            except RuntimeError:
                return False
            except Exception:
                return False
        if not person:
            return False
        return _can_access_equip_stats(person)

    # ---------------------------------------------------- patch helpers
    app_module.TEAM_LEAD_MAP = TEAM_LEAD_MAP
    app_module.EQUIP_FIX_PAGE_SIZE = EQUIP_FIX_PAGE_SIZE
    app_module.AS_STAFF_MAX = AS_STAFF_MAX
    app_module._receipt_extras = _receipt_extras
    app_module._set_receipt_extras = _set_receipt_extras
    app_module._normalize_as_staff_fields = _normalize_as_staff_fields
    app_module._as_staff_codes = _as_staff_codes
    app_module._set_as_staff_list = _set_as_staff_list
    app_module._sync_as_staff_list_primary = _sync_as_staff_list_primary
    app_module._can_multi_assign_as = _can_multi_assign_as
    app_module._validate_as_staff_assign_codes = _validate_as_staff_assign_codes
    app_module._can_access_equip_stats = _can_access_equip_stats
    app_module.MULTI_ASSIGN_DENIED_MSG = MULTI_ASSIGN_DENIED_MSG
    app_module.AS_STAFF_FULL_MSG = AS_STAFF_FULL_MSG
    app_module._can_manage_equip_fixes = _can_manage_equip_fixes
    app_module._equip_norm_key = _equip_norm_key
    app_module._equip_series_base = _equip_series_base
    app_module._list_equip_fixes = _list_equip_fixes
    app_module.SUPPORT_SLA_DAYS = SUPPORT_SLA_DAYS
    app_module._auto_confirm_support_on_view = _auto_confirm_support_on_view
    app_module._pending_transfers_for_person = _pending_transfers_for_person
    app_module._transfer_blocked_reason = _transfer_blocked_reason
    app_module._transfer_as_candidates = _transfer_as_candidates
    app_module._can_decide_transfer = _can_decide_transfer
    app_module._ensure_support_feedback = _ensure_support_feedback
    app_module._support_open_items = _support_open_items
    app_module._handling_team_id = _handling_team_id

    # 팀장도 기술공유 UI 노출 (실제 공유는 overdue/긴급 검사)
    orig_can_share_tech = app_module._can_share_tech_support

    def _can_share_tech_support(person=None):
        if orig_can_share_tech(person):
            return True
        if person is None:
            try:
                person = app_module._get_person_for_session()
            except Exception:
                person = None
        return bool(_is_team_lead(person))

    app_module._can_share_tech_support = _can_share_tech_support

    # wrap _share_support_kind — init feedback + team-lead tech rules
    orig_share = app_module._share_support_kind

    def _share_support_kind(receipt, kind, person, submitted_token=None):
        kind = (kind or "").strip()
        if kind == "tech" and receipt and person:
            is_head = False
            try:
                is_head = bool(
                    app_module._is_sales_head(person)
                    or session.get("user_role") == "admin"
                )
            except Exception:
                pass
            is_quality = False
            try:
                is_quality = bool(app_module._is_quality_support_person(person))
            except Exception:
                pass
            if not is_head and not is_quality and _is_team_lead(person):
                # 팀장: 품질 지연≥SLA일(대기/확인) 또는 긴급(사유)
                # JSON(/api/support-share) · form 모두 허용
                payload = {}
                try:
                    payload = request.get_json(silent=True) or {}
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}

                def _req_val(*keys):
                    for k in keys:
                        v = request.form.get(k)
                        if v:
                            return v
                        v = payload.get(k)
                        if v:
                            return v
                    return ""

                urgent = bool(_req_val("urgent_tech", "urgent"))
                reason = str(
                    _req_val("urgent_reason", "reopen_note", "reason") or ""
                ).strip()
                no = receipt.get("receipt_no") or ""
                detail = app_module.REPORT_DETAILS.get(no) or {}
                fb = (detail.get("support_feedback") or {}).get("quality") or {}
                delayed = _feedback_is_overdue(fb) and bool(receipt.get("support_quality"))
                if urgent and reason:
                    # 긴급 직행: 품질 공유·지연 무관
                    pass
                elif not receipt.get("support_quality"):
                    return {
                        "ok": False,
                        "error": "품질팀에 공유된 건만 기술 지원으로 이관할 수 있습니다. 긴급이면 「긴급 직행」을 사용하세요.",
                    }
                elif not delayed:
                    return {
                        "ok": False,
                        "error": f"팀장 기술공유는 품질팀 미회신 {SUPPORT_SLA_DAYS}일 이상 또는 긴급(사유)일 때만 가능합니다.",
                    }
                if urgent and not reason:
                    return {"ok": False, "error": "긴급 사유를 입력해 주세요."}
                # temporarily allow by acting as head path via flag
                result = orig_share(receipt, kind, person, submitted_token)
                # If orig blocks team lead, call internal share path
                if not result.get("ok") and "권한" in (result.get("error") or ""):
                    # force share like quality path
                    if receipt.get("support_tech"):
                        return {"ok": True, "already": True, "mark": "공유됨", "kind": kind}
                    receipt["support_tech"] = True
                    detail_row = _detail_bucket(no)
                    detail_row["support_tech"] = True
                    app_module._append_support_note(
                        receipt, f"{app_module._support_tech_dept_label(receipt)} 지원 요청 ({_today_iso()})"
                    )
                    _ensure_support_feedback(
                        receipt, "tech", urgent=urgent, reopen_note=reason
                    )
                    app_module._save_service_data()
                    try:
                        app_module._notify_support_request(
                            {**receipt, "support_quality": False, "support_tech": True},
                            requester=(person or {}).get("name") or "담당자",
                        )
                    except Exception:
                        pass
                    return {
                        "ok": True,
                        "already": False,
                        "mark": "공유됨",
                        "kind": kind,
                        "message": "기술 지원부서에 긴급/조건부 공유했습니다.",
                    }
                if result.get("ok") and not result.get("already"):
                    _ensure_support_feedback(
                        receipt, "tech", urgent=urgent, reopen_note=reason
                    )
                    app_module._save_service_data()
                return result

        result = orig_share(receipt, kind, person, submitted_token)
        if result.get("ok") and not result.get("already") and kind in ("quality", "tech"):
            _ensure_support_feedback(receipt, kind)
            try:
                app_module._save_service_data()
            except Exception:
                pass
        return result

    app_module._share_support_kind = _share_support_kind

    # 지원요청 알림 시 feedback 엔트리 보장
    orig_notify_support = getattr(app_module, "_notify_support_request", None)

    def _notify_support_request(receipt, requester=""):
        if receipt:
            no = receipt.get("receipt_no") or ""
            real = app_module._get_receipt_by_no(no) if no else receipt
            real = real or receipt
            if receipt.get("support_quality"):
                _ensure_support_feedback(real, "quality")
            if receipt.get("support_tech"):
                _ensure_support_feedback(real, "tech")
        if orig_notify_support:
            return orig_notify_support(receipt, requester=requester)
        return None

    if orig_notify_support:
        app_module._notify_support_request = _notify_support_request

    # wrap status report
    orig_status = app_module._build_status_report

    def _build_status_report(year=None, period="month"):
        report = orig_status(year=year, period=period)
        year = report.get("year") or date.today().year
        period = report.get("period") or "month"
        period_rows = (report.get("overall") or {}).get("period_rows") or []
        period_keys = [row["key"] for row in period_rows]
        period_labels = {
            row["key"]: (row.get("short_label") or row.get("label") or row["key"])
            for row in period_rows
        }
        if not period_keys:
            try:
                keys, labels, short = app_module._build_period_axis(
                    period, year, report.get("year_options") or [year]
                )
                period_keys = keys
                period_labels = {k: short.get(k) or labels.get(k) or k for k in keys}
            except Exception:
                period_keys = []
                period_labels = {}
        person = None
        try:
            person = app_module._get_person_for_session()
        except Exception:
            person = None
        if _can_view_staff_performance(person):
            segments, totals, flat_rows = _build_staff_performance(
                year, period, period_keys, period_labels
            )
            report["staff_performance_segments"] = segments
            report["staff_performance"] = flat_rows
            report["staff_performance_total"] = totals
            report["other_team_support_note"] = (
                "상단 월/분기/연 탭에 맞춰 구간별로 표시합니다. "
                "타팀 지원 건은 담당자명 옆에 「(타팀건 N건 지원 포함)」으로 표기됩니다."
            )
            report["staff_team_agg_separated"] = True
        report["support_requests"] = _support_open_items()
        report["support_sla_days"] = SUPPORT_SLA_DAYS
        return report

    app_module._build_status_report = _build_status_report
    app_module._can_view_staff_performance = _can_view_staff_performance
    app_module._build_staff_performance = _build_staff_performance

    # wrap home dashboard
    orig_home = app_module._build_home_dashboard

    def _build_home_dashboard():
        dash = orig_home()
        person = app_module._get_person_for_session()
        pending = _pending_transfers_for_person(person)
        dash["pending_transfers"] = pending
        dash["pending_transfer_count"] = len(pending)
        return dash

    app_module._build_home_dashboard = _build_home_dashboard

    # wrap history GET — 완료건은 AS보고서로 유도 · 미완료만 이력에서 자동확인
    orig_history = app_module.history

    def history():
        person = app_module._get_person_for_session()
        selected = (request.args.get("receipt_no") or "").strip()
        if selected and person and person.get("team_code") == "field_support":
            receipt = app_module._get_receipt_by_no(selected)
            if receipt:
                completed = False
                try:
                    completed = bool(app_module._is_receipt_completed(receipt))
                except Exception:
                    completed = (receipt.get("status") or "") == "completed"
                if completed:
                    flash(
                        "완료된 건의 지원 회신은 AS보고서 화면에서 진행해 주세요. (AS내용은 잠금 유지)",
                        "info",
                    )
                    try:
                        target = (
                            "rods_report"
                            if app_module._is_rods()
                            else "report"
                        )
                    except Exception:
                        target = "report"
                    return redirect(url_for(target, receipt_no=selected))
                if _auto_confirm_support_on_view(receipt, person):
                    try:
                        app_module._save_service_data()
                    except Exception:
                        pass
        return orig_history()

    app_module.history = history
    app.view_functions["history"] = history

    # wrap report / api_report — 지원부서 열람 시 자동 확인함 (완료 건 포함)
    def _maybe_auto_confirm_on_report(receipt_no):
        person = app_module._get_person_for_session()
        if not person or not receipt_no:
            return
        receipt = app_module._get_receipt_by_no(receipt_no)
        if receipt and _auto_confirm_support_on_view(receipt, person):
            try:
                app_module._save_service_data()
            except Exception:
                pass

    orig_report = app_module.report

    def report():
        selected = (request.args.get("receipt_no") or "").strip()
        if selected:
            _maybe_auto_confirm_on_report(selected)
        return orig_report()

    app_module.report = report
    app.view_functions["report"] = report

    orig_api_report = app_module.api_report

    def api_report(receipt_no):
        _maybe_auto_confirm_on_report(receipt_no)
        return orig_api_report(receipt_no)

    app_module.api_report = api_report
    app.view_functions["api_report"] = api_report

    # 8-2: 신규 접수·수정 폼은 AS담당 1명만 (공동지정은 지정·변경 API만)
    orig_sales_register = app_module.sales_register

    def sales_register():
        prev_no = session.get("last_worked_receipt_no") or ""
        resp = orig_sales_register()
        try:
            no = session.get("last_worked_receipt_no") or ""
            if not no or no == prev_no:
                return resp
            receipt = app_module._get_receipt_by_no(no)
            if receipt:
                code = (receipt.get("as_staff") or "").strip()
                _set_as_staff_list(receipt, [code] if code else [])
                app_module._save_service_data()
        except Exception:
            pass
        return resp

    app_module.sales_register = sales_register
    app.view_functions["sales_register"] = sales_register

    orig_sales_update = app_module.sales_update

    def sales_update(receipt_no):
        before = app_module._get_receipt_by_no(receipt_no)
        old_primary = (before.get("as_staff") or "").strip() if before else ""
        old_codes = list(_as_staff_codes(before)) if before else []
        old_name = (before.get("as_staff_name") or "") if before else ""
        resp = orig_sales_update(receipt_no)
        try:
            # 권한/검증 실패로 수정이 안 된 경우(리다이렉트 flash error)에도
            # 접수 객체가 바뀌었을 수 있어 primary 비교로만 처리
            receipt = app_module._get_receipt_by_no(receipt_no)
            if not receipt:
                return resp
            new_primary = (receipt.get("as_staff") or "").strip()
            if new_primary == old_primary and len(old_codes) > 1:
                _set_as_staff_list(receipt, old_codes)
                receipt["as_staff_name"] = old_name or receipt.get("as_staff_name")
                app_module._save_service_data()
            elif new_primary != old_primary or len(old_codes) <= 1:
                # 폼에서 담당자를 바꾸면 1명으로 확정(공동지정은 지정·변경만)
                if new_primary != old_primary:
                    _set_as_staff_list(receipt, [new_primary] if new_primary else [])
                    app_module._save_service_data()
                elif len(old_codes) <= 1:
                    _set_as_staff_list(receipt, [new_primary] if new_primary else [])
                    app_module._save_service_data()
        except Exception:
            pass
        return resp

    app_module.sales_update = sales_update
    app.view_functions["sales_update"] = sales_update

    # wrap sales to pass can_multi flag
    orig_sales = app_module.sales

    def sales():
        resp = orig_sales()
        return resp

    # context processor
    @app.context_processor
    def inject_0905_globals():
        person = None
        can_equip = False
        pending_count = 0
        can_multi = False
        try:
            if session.get("user_id"):
                person = app_module._get_person_for_session()
                can_equip = _can_access_equip_stats(person)
                pending_count = len(_pending_transfers_for_person(person))
                can_multi = bool(_can_multi_assign_as(None, person))
        except Exception:
            pass
        is_quality = False
        is_tech = False
        try:
            if person:
                is_quality = bool(app_module._is_quality_support_person(person))
                is_tech = bool(app_module._is_tech_support_person(person))
        except Exception:
            pass
        is_team_lead_nav = False
        try:
            if person:
                is_team_lead_nav = bool(
                    app_module._is_sales_team_leader(person) or _is_team_lead(person)
                )
        except Exception:
            is_team_lead_nav = False
        return {
            "current_user_can_equip_stats": can_equip,
            "current_user_is_sales_team_leader": is_team_lead_nav,
            "pending_transfer_count": pending_count,
            "current_user_can_multi_assign": can_multi,
            "as_staff_max": AS_STAFF_MAX,
            "multi_assign_denied_msg": MULTI_ASSIGN_DENIED_MSG,
            "current_user_is_quality_support": is_quality,
            "current_user_is_tech_support": is_tech,
        }

    # ---------------------------------------------------- routes: transfer
    @app.route("/api/transfer/candidates")
    @app_module._login_required
    def api_transfer_candidates():
        team = (request.args.get("team") or "").strip()
        if team not in TEAM_LEAD_MAP:
            return jsonify({"error": "팀 코드 오류", "items": []}), 400
        return jsonify(
            {
                "team": team,
                "team_label": _team_label(team),
                "lead_id": TEAM_LEAD_MAP.get(team) or "",
                "items": _transfer_as_candidates(team),
            }
        )

    @app.route("/api/transfer/request", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_transfer_request():
        person = app_module._get_person_for_session()
        receipt_no = (request.form.get("receipt_no") or "").strip()
        to_team = (request.form.get("to_team") or "").strip()
        proposed = (request.form.get("proposed_as_staff") or "").strip()
        reason = (request.form.get("reason") or "").strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not app_module._can_reassign_as_staff(receipt, person):
            return jsonify({"error": "이관 요청 권한이 없습니다."}), 403
        blocked = _transfer_blocked_reason(receipt)
        if blocked:
            return jsonify({"error": blocked}), 400
        if to_team not in TEAM_LEAD_MAP:
            return jsonify({"error": "이관 대상 팀이 올바르지 않습니다."}), 400
        from_team = _handling_team_id(receipt) or receipt.get("team_id") or ""
        if to_team == from_team:
            return jsonify({"error": "동일 팀으로는 이관할 수 없습니다."}), 400
        if proposed:
            allowed = {c["id"] for c in _transfer_as_candidates(to_team)}
            if proposed not in allowed:
                return jsonify({"error": "이관 후 담당자 후보가 올바르지 않습니다."}), 400
            if not reason:
                return jsonify(
                    {"error": "담당자를 지정하려면 이관 사유를 입력해 주세요."}
                ), 400
        proposed_name = _org_name(proposed) if proposed else ""
        now = _now_iso()
        with app_module._user_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO team_transfers (
                    receipt_no, from_team, to_team, proposed_as_staff, proposed_as_name,
                    reason, status, requested_by, requested_by_name, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    receipt_no,
                    from_team,
                    to_team,
                    proposed,
                    proposed_name,
                    reason,
                    _person_code(person),
                    (person or {}).get("name") or session.get("user_name") or "",
                    now,
                ),
            )
            tid = cur.lastrowid
        _set_receipt_extras(
            receipt,
            transfer_pending_id=tid,
            transfer_pending_to=to_team,
        )
        app_module._save_service_data()
        _notify_transfer_team(to_team, receipt_no, from_team, reason)
        return jsonify({"ok": True, "transfer_id": tid})

    @app.route("/api/transfer/decide", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_transfer_decide():
        person = app_module._get_person_for_session()
        try:
            transfer_id = int(request.form.get("transfer_id") or 0)
        except ValueError:
            return jsonify({"error": "잘못된 요청"}), 400
        decision = (request.form.get("decision") or "").strip().lower()
        note = (request.form.get("note") or "").strip()
        if decision not in ("approve", "reject"):
            return jsonify({"error": "승인 또는 거절만 가능합니다."}), 400
        with app_module._user_db() as conn:
            row = conn.execute(
                "SELECT * FROM team_transfers WHERE id = ?", (transfer_id,)
            ).fetchone()
        if not row:
            return jsonify({"error": "이관 요청 없음"}), 404
        item = dict(row)
        if item.get("status") != "pending":
            return jsonify({"error": "이미 처리된 요청입니다."}), 400
        if not _can_decide_transfer(item, person):
            return jsonify({"error": "이관 승인 권한이 없습니다."}), 403

        receipt = app_module._get_receipt_by_no(item.get("receipt_no") or "")
        now = _now_iso()
        code = _person_code(person)
        new_status = "approved" if decision == "approve" else "rejected"
        with app_module._user_db() as conn:
            conn.execute(
                """
                UPDATE team_transfers
                SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?
                WHERE id = ?
                """,
                (new_status, code, now, note, transfer_id),
            )

        if receipt:
            _set_receipt_extras(
                receipt, transfer_pending_id=None, transfer_pending_to=""
            )
            req = (item.get("requested_by") or "").strip()
            if decision == "approve":
                to_team = item.get("to_team") or ""
                origin = item.get("from_team") or receipt.get("team_id") or ""
                extras = _receipt_extras(receipt)
                origin_tag = (extras.get("origin_team_tag") or "").strip()
                if not origin_tag:
                    origin_tag = f"[{_team_label(origin)}접수]"
                _set_receipt_extras(
                    receipt,
                    handling_team_id=to_team,
                    origin_team_tag=origin_tag,
                )
                _set_as_staff_list(receipt, [], person)
                _clear_service_technician(receipt)
                proposed = (item.get("proposed_as_staff") or "").strip()
                auto_lead = not proposed
                assignee = proposed or (TEAM_LEAD_MAP.get(to_team) or "")
                if assignee:
                    _set_as_staff_list(receipt, [assignee], person)
                    detail = app_module.REPORT_DETAILS.get(
                        receipt.get("receipt_no") or ""
                    )
                    if detail is not None:
                        detail["as_technician"] = (
                            receipt.get("as_staff_name") or _org_name(assignee)
                        )
                app_module._save_service_data()
                if assignee:
                    msg = "[담당자 지정] 이관 승인에 따라 AS담당자로 지정되었습니다"
                    if auto_lead:
                        msg += (
                            "\n담당자가 별도 지정되지 않아 팀장이 자동 배정되었습니다"
                        )
                    try:
                        app_module.notify_staff_assigned(
                            receipt,
                            assignee,
                            "AS 담당자",
                            app_module._get_staff_email,
                            app_module._mail_base_url(),
                        )
                    except Exception:
                        pass
                    try:
                        app_module._push_notification(
                            user_code=assignee,
                            title=msg.split("\n")[0],
                            body=msg,
                            link="/history",
                            receipt_no=receipt.get("receipt_no") or "",
                        )
                    except Exception:
                        pass
                if req:
                    try:
                        app_module._push_notification(
                            user_code=req,
                            title=f"[이관승인] {item.get('receipt_no')}",
                            body=(
                                f"{_team_label(to_team)}으로 이관이 승인되었습니다."
                                + (f" 담당: {_org_name(assignee)}" if assignee else "")
                            ),
                            link="/home",
                            receipt_no=item.get("receipt_no") or "",
                        )
                    except Exception:
                        pass
            else:
                app_module._save_service_data()
                if req:
                    try:
                        app_module._push_notification(
                            user_code=req,
                            title=f"[이관거절] {item.get('receipt_no')}",
                            body=note or "이관 요청이 거절되었습니다.",
                            link="/home",
                            receipt_no=item.get("receipt_no") or "",
                        )
                    except Exception:
                        pass
        return jsonify({"ok": True, "status": new_status})

    # ---------------------------------------------------- support APIs
    @app.route("/api/support/reply", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_support_reply():
        person = app_module._get_person_for_session()
        receipt_no = (request.form.get("receipt_no") or "").strip()
        kind = (request.form.get("kind") or "").strip()
        reply = (request.form.get("reply") or "").strip()
        if kind not in ("quality", "tech"):
            return jsonify({"error": "잘못된 유형"}), 400
        if not reply:
            return jsonify({"error": "회신 내용을 입력해 주세요."}), 400
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        allowed = False
        try:
            if kind == "quality" and app_module._is_quality_support_person(person):
                allowed = True
            if kind == "tech" and app_module._is_tech_support_person(person):
                allowed = True
            if session.get("user_role") == "admin":
                allowed = True
        except Exception:
            pass
        if not allowed:
            return jsonify({"error": "회신 권한이 없습니다."}), 403
        detail = _detail_bucket(receipt_no)
        fb = dict(detail.get("support_feedback") or {})
        entry = dict(fb.get(kind) or dict(_FEEDBACK_BLANK))
        if not entry.get("shared_at"):
            entry["shared_at"] = _now_iso()
        entry["status"] = "replied"
        entry["replied_at"] = _now_iso()
        entry["reply"] = reply
        if not entry.get("confirmed_at"):
            entry["confirmed_at"] = entry["replied_at"]
        fb[kind] = entry
        detail["support_feedback"] = fb
        label = "품질팀" if kind == "quality" else "기술부"
        app_module._append_support_note(
            receipt, f"{label} 회신 ({_today_iso()}): {reply[:120]}"
        )
        app_module._save_service_data()
        # notify primary as staff (완료 건은 문구·링크 분기 — AS내용/수정이력 미갱신)
        is_completed = False
        try:
            is_completed = bool(app_module._is_receipt_completed(receipt))
        except Exception:
            is_completed = (receipt.get("status") or "") == "completed"
        report_link = f"/report?receipt_no={receipt_no}"
        try:
            if app_module._is_rods():
                report_link = f"/rods/report?receipt_no={receipt_no}"
        except Exception:
            pass
        for ac in _as_staff_codes(receipt)[:1]:
            try:
                if is_completed:
                    app_module._push_notification(
                        user_code=ac,
                        title=f"[완료 건 회신] {receipt_no}",
                        body=f"{label} 완료 건 회신: {reply[:90]}",
                        link=report_link,
                        receipt_no=receipt_no,
                    )
                else:
                    app_module._push_notification(
                        user_code=ac,
                        title=f"[지원회신] {receipt_no}",
                        body=f"{label}: {reply[:100]}",
                        link="/history",
                        receipt_no=receipt_no,
                    )
            except Exception:
                pass
        return jsonify({"ok": True, "status": "replied", "completed_case": is_completed})

    @app.route("/api/support/reject-to-quality", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_support_reject_to_quality():
        person = app_module._get_person_for_session()
        receipt_no = (request.form.get("receipt_no") or "").strip()
        note = (request.form.get("note") or request.form.get("reply") or "").strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        allowed = False
        try:
            allowed = bool(
                app_module._is_tech_support_person(person)
                or session.get("user_role") == "admin"
            )
        except Exception:
            pass
        if not allowed:
            return jsonify({"error": "반려 권한이 없습니다."}), 403
        if not note:
            return jsonify({"error": "반려 사유를 입력해 주세요."}), 400
        receipt["support_tech"] = False
        detail = _detail_bucket(receipt_no)
        detail["support_tech"] = False
        fb = dict(detail.get("support_feedback") or {})
        tech = dict(fb.get("tech") or dict(_FEEDBACK_BLANK))
        tech["status"] = "rejected"
        tech["replied_at"] = _now_iso()
        tech["reply"] = note
        fb["tech"] = tech
        quality = dict(fb.get("quality") or dict(_FEEDBACK_BLANK))
        quality["status"] = "waiting"
        quality["shared_at"] = _now_iso()
        quality["reopen_note"] = f"🔁 [기술부 반려] {note}"
        quality["confirmed_at"] = ""
        quality["replied_at"] = ""
        quality["reply"] = ""
        fb["quality"] = quality
        detail["support_feedback"] = fb
        if not receipt.get("support_quality"):
            receipt["support_quality"] = True
            detail["support_quality"] = True
        app_module._append_support_note(
            receipt, f"🔁 [기술부 반려] ({_today_iso()}): {note[:100]}"
        )
        app_module._save_service_data()
        try:
            app_module._push_notification(
                user_code="quality_acc",
                title=f"[기술반려] {receipt_no}",
                body=f"🔁 [기술부 반려] {note[:120]}",
                link="/history",
                receipt_no=receipt_no,
            )
        except Exception:
            pass
        for ac in _as_staff_codes(receipt)[:1]:
            try:
                app_module._push_notification(
                    user_code=ac,
                    title=f"[기술반려] {receipt_no}",
                    body=f"기술부에서 품질팀으로 반려했습니다. {note[:80]}",
                    link="/history",
                    receipt_no=receipt_no,
                )
            except Exception:
                pass
        tech_req = (tech.get("requested_by_name") or "").strip()
        if tech_req:
            for ppl in app_module._get_org_people():
                if (ppl.get("name") or "") == tech_req:
                    code = ppl.get("id") or ""
                    if code and code not in set(_as_staff_codes(receipt)[:1]):
                        try:
                            app_module._push_notification(
                                user_code=code,
                                title=f"[기술반려] {receipt_no}",
                                body=f"기술부에서 품질팀으로 반려했습니다. {note[:80]}",
                                link="/history",
                                receipt_no=receipt_no,
                            )
                        except Exception:
                            pass
                    break
        return jsonify({"ok": True})

    @app.route("/api/support/urgent-tech", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_support_urgent_tech():
        """팀장 긴급 직행 — 품질 경유 없이 즉시 기술부 요청(사유 필수)."""
        person = app_module._get_person_for_session()
        receipt_no = (request.form.get("receipt_no") or "").strip()
        reason = (request.form.get("reason") or "").strip()
        if not reason:
            return jsonify({"error": "긴급 사유를 입력해 주세요."}), 400
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not (
            _is_team_lead(person)
            or app_module._is_sales_head(person)
            or session.get("user_role") == "admin"
        ):
            return jsonify({"error": "권한이 없습니다."}), 403
        if receipt.get("support_tech"):
            return jsonify({"ok": True, "already": True})
        receipt["support_tech"] = True
        detail = _detail_bucket(receipt_no)
        detail["support_tech"] = True
        try:
            tech_label = app_module._support_tech_dept_label(receipt)
        except Exception:
            tech_label = "기술부"
        app_module._append_support_note(
            receipt,
            f"🚨 긴급직행 {tech_label} 지원 요청 ({_today_iso()}): {reason[:80]}",
        )
        req_name = (person or {}).get("name") or "담당자"
        _ensure_support_feedback(
            receipt,
            "tech",
            urgent=True,
            reopen_note=reason,
            requested_by_name=req_name,
        )
        app_module._save_service_data()
        try:
            app_module._notify_support_request(
                {**receipt, "support_quality": False, "support_tech": True},
                requester=req_name,
            )
        except Exception:
            pass
        try:
            app_module._push_notification(
                user_code="quality_acc",
                title=f"[긴급직행 참고] {receipt_no}",
                body=(
                    f"팀장({req_name})이 품질팀 경유 없이 기술부에 직접 요청했습니다. "
                    f"사유: {reason[:100]}"
                ),
                link="/history",
                receipt_no=receipt_no,
            )
        except Exception:
            pass
        return jsonify({"ok": True})

    # ---------------------------------------------------- assign-as replace
    def api_sales_assign_as():
        person = app_module._get_person_for_session()
        receipt_no = request.form.get("receipt_no", "").strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not app_module._can_reassign_as_staff(receipt, person):
            return jsonify({"error": "권한 없음"}), 403
        if (
            app_module._is_receipt_completed(receipt)
            and session.get("user_role") != "admin"
        ):
            return jsonify(
                {"error": "완료된 건의 AS 담당자는 관리자만 변경할 수 있습니다."}
            ), 403

        # multi: repeated keys or comma-separated
        raw_list = request.form.getlist("as_staff")
        if len(raw_list) <= 1:
            single = (request.form.get("as_staff") or "").strip()
            if "," in single:
                raw_list = [x.strip() for x in single.split(",") if x.strip()]
            elif single:
                raw_list = [single]
            else:
                raw_list = [x for x in raw_list if (x or "").strip()]
        codes = []
        for x in raw_list:
            for part in str(x).split(","):
                part = part.strip()
                if part and part not in codes:
                    codes.append(part)

        if len(codes) > AS_STAFF_MAX:
            return jsonify({"error": AS_STAFF_FULL_MSG}), 400

        deny = _validate_as_staff_assign_codes(receipt, person, codes)
        if deny:
            return jsonify({"error": deny}), 403

        old_codes = set(_as_staff_codes(receipt))
        if receipt.get("team_id") == "export":
            if not (
                app_module._is_sales_head(person)
                or session.get("user_role") == "admin"
            ):
                if not codes:
                    app_module._assign_export_as_handler(receipt)
                    codes = _as_staff_codes(receipt)
                else:
                    _set_as_staff_list(receipt, codes, person)
            else:
                _set_as_staff_list(receipt, codes, person)
        else:
            _set_as_staff_list(receipt, codes, person)

        app_module._save_service_data()
        new_codes = _as_staff_codes(receipt)
        added = [c for c in new_codes if c not in old_codes]
        co_note = ""
        if len(new_codes) > 1:
            names = " · ".join(_org_name(c) for c in new_codes)
            co_note = f" (공동 담당 {names})"
        for ac in added:
            try:
                app_module._notify_as_staff_reassigned(
                    receipt, ac, co_note=co_note
                )
            except Exception:
                pass
        return jsonify(
            {
                "ok": True,
                "as_staff": receipt.get("as_staff"),
                "as_staff_ids": new_codes,
                "as_staff_name": receipt.get("as_staff_name"),
            }
        )

    app.view_functions["api_sales_assign_as"] = api_sales_assign_as

    # ---------------------------------------------------- equip stats routes
    def _equip_page_context(person, *, detail_name="", scroll_detail=False):
        groups = _build_equip_groups()
        ranking = [
            {k: v for k, v in g.items() if k not in ("receipts", "part_counter")}
            for g in groups
        ]
        q = (request.args.get("q") or "").strip()
        if q:
            q_cf = q.casefold()
            ranking = [
                g
                for g in ranking
                if q_cf in (g.get("name") or "").casefold()
            ]
        can_manage = _can_manage_equip_fixes(person)
        candidates = _equip_similar_candidates(groups)[:40] if can_manage else []
        fix_page = request.args.get("fix_page", "1")
        fix_q = request.args.get("fix_q", "")
        fixes = _list_equip_fixes(fix_page, fix_q)
        detail = None
        if detail_name:
            detail = _equip_detail_payload(detail_name, groups)
        return {
            "active_page": "equip_stats",
            "ranking": ranking,
            "rank_q": q,
            "candidates": candidates,
            "fixes": fixes,
            "can_manage_fixes": can_manage,
            "detail": detail,
            "scroll_detail": bool(scroll_detail and detail),
        }

    @app.route("/equip-stats")
    @app_module._login_required
    def equip_stats():
        person = app_module._get_person_for_session()
        if not _can_access_equip_stats(person):
            flash("장비별 AS통계 접근 권한이 없습니다.", "error")
            return redirect(url_for("home"))
        try:
            if app_module._is_rods():
                flash("용접봉 라인에서는 장비 통계를 제공하지 않습니다.", "error")
                return redirect(url_for("rods_home"))
        except Exception:
            pass
        detail_name = (request.args.get("detail") or "").strip()
        scroll = (request.args.get("scroll") or "").strip() in ("1", "true", "yes")
        ctx = _equip_page_context(
            person, detail_name=detail_name, scroll_detail=scroll
        )
        return render_template("equip_stats.html", **ctx)

    @app.route("/equip-stats/detail")
    @app_module._login_required
    def equip_stats_detail():
        """호환: 상세 클릭은 scroll=1 로 메인 화면에 표시."""
        person = app_module._get_person_for_session()
        if not _can_access_equip_stats(person):
            flash("장비별 AS통계 접근 권한이 없습니다.", "error")
            return redirect(url_for("home"))
        name = (request.args.get("name") or "").strip()
        scroll = (request.args.get("scroll") or "1").strip()
        if not name:
            return redirect(url_for("equip_stats"))
        return redirect(
            url_for("equip_stats", detail=name, scroll=scroll or "1")
        )

    @app.route("/equip-stats/export.csv")
    @app_module._login_required
    def equip_stats_export_csv():
        person = app_module._get_person_for_session()
        if not _can_access_equip_stats(person):
            return "권한 없음", 403
        groups = _build_equip_groups()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "순위",
                "장비명",
                "건수",
                "유상",
                "무상",
                "재수리율(%)",
                "워런티비율(%)",
                "최다교체부품",
                "수량",
            ]
        )
        for i, g in enumerate(groups, start=1):
            writer.writerow(
                [
                    i,
                    g["name"],
                    g["total"],
                    g["paid"],
                    g["free"],
                    g["re_repair_rate"],
                    g["warranty_rate"],
                    g["top_part"],
                    g.get("top_part_count") or 0,
                ]
            )
        data = "\ufeff" + buf.getvalue()
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=equip_as_stats.csv"
            },
        )

    @app.route("/equip-stats/fix", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def equip_stats_fix():
        person = app_module._get_person_for_session()
        if not _can_manage_equip_fixes(person):
            return jsonify(
                {"error": "장비명 수정은 부서장·총괄·대표이사 또는 관리자만 가능합니다."}
            ), 403
        action = (request.form.get("action") or "add").strip()
        if action == "delete":
            try:
                fid = int(request.form.get("id") or 0)
            except ValueError:
                return jsonify({"error": "잘못된 ID"}), 400
            with app_module._user_db() as conn:
                conn.execute("DELETE FROM equip_name_fixes WHERE id = ?", (fid,))
            return jsonify({"ok": True, "redirect_detail": (request.form.get("detail") or "").strip()})
        raw_name = (request.form.get("raw_name") or "").strip()
        standard_name = (request.form.get("standard_name") or "").strip()
        if not raw_name or not standard_name:
            return jsonify({"error": "원본명과 표준명을 입력해 주세요."}), 400
        with app_module._user_db() as conn:
            conn.execute(
                """
                INSERT INTO equip_name_fixes (raw_name, standard_name, created_by, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    raw_name,
                    standard_name,
                    (person or {}).get("name") or session.get("username") or "",
                    _now_iso(),
                ),
            )
        return jsonify(
            {
                "ok": True,
                "redirect_detail": (request.form.get("detail") or "").strip(),
            }
        )

    @app.route("/api/equip-stats/candidates")
    @app_module._login_required
    def api_equip_candidates():
        person = app_module._get_person_for_session()
        if not _can_manage_equip_fixes(person):
            return jsonify({"error": "권한 없음"}), 403
        return jsonify({"items": _equip_similar_candidates()[:50]})

    # enrich receipt with handling / co-staff display
    orig_enrich = app_module._enrich_receipt

    def _enrich_receipt(receipt, all_receipts, *, skip_queue=False, queue_team_scope=None):
        base = orig_enrich(
            receipt,
            all_receipts,
            skip_queue=skip_queue,
            queue_team_scope=queue_team_scope,
        )
        extras = _receipt_extras(receipt)
        handling = extras.get("handling_team_id") or ""
        if handling and handling != (receipt.get("team_id") or ""):
            base["handling_team_id"] = handling
            base["handling_team_name"] = _team_label(handling)
        origin = extras.get("origin_team_tag") or ""
        if origin:
            base["origin_tag"] = origin
            base["origin_team_tag"] = origin
        ids = extras.get("as_staff_ids") or []
        if ids:
            base["as_staff_ids"] = ids
            base["as_staff_names"] = extras.get("as_staff_names") or []
            base["as_display"] = receipt.get("as_staff_name") or base.get("as_display")
        base["transfer_pending_id"] = extras.get("transfer_pending_id")
        pending_to = extras.get("transfer_pending_to") or ""
        base["transfer_pending_to"] = pending_to
        base["transfer_pending_label"] = (
            f"→ {_team_label(pending_to)} 이관 승인 대기중" if pending_to else ""
        )
        if not base["transfer_pending_label"] and base["transfer_pending_id"]:
            base["transfer_pending_label"] = "→ 이관 승인 대기중"
        person = None
        try:
            person = app_module._get_person_for_session()
        except Exception:
            person = None
        base["can_multi_assign_as"] = _can_multi_assign_as(receipt, person)
        return base

    app_module._enrich_receipt = _enrich_receipt

    orig_get_receipt = app_module._get_receipt_by_no

    def _get_receipt_by_no(receipt_no):
        receipt = orig_get_receipt(receipt_no)
        if not receipt:
            return receipt
        extras = _receipt_extras(receipt)
        receipt["handling_team_id"] = (
            extras.get("handling_team_id") or receipt.get("team_id") or ""
        )
        receipt["origin_team_tag"] = extras.get("origin_team_tag") or ""
        receipt["transfer_pending_id"] = extras.get("transfer_pending_id")
        receipt["transfer_pending_to"] = extras.get("transfer_pending_to") or ""
        ids = extras.get("as_staff_ids") or []
        if ids:
            receipt["as_staff_ids"] = ids
            receipt["as_staff_names"] = extras.get("as_staff_names") or []
        fb = extras.get("support_feedback")
        if fb:
            receipt["support_feedback"] = fb
        return receipt

    app_module._get_receipt_by_no = _get_receipt_by_no
    app_module._as_ext_0905_installed = True

    return True
