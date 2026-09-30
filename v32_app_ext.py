# -*- coding: utf-8 -*-
"""v3.2 확장: 알림·과거이력·청구·부품·자동감지 API 및 권한 헬퍼."""

from __future__ import annotations

import json
from datetime import datetime

from flask import flash, jsonify, redirect, request, session, url_for

import v32_rules as v32


def install(app_module):
    """app.py 모듈에 헬퍼·라우트를 주입한다."""
    app = app_module.app

    def _scoped_receipts(source=None, receipt=None):
        rows = source if source is not None else app_module.RECEIPTS
        try:
            if receipt is not None:
                import product_line_ext as plex
                pl = plex.normalize_receipt_line(receipt.get("product_line"))
                return [
                    r
                    for r in rows
                    if plex.normalize_receipt_line(r.get("product_line")) == pl
                ]
            return app_module._line_receipts(rows)
        except Exception:
            return rows

    def _is_quality_support_person(person=None):
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return False
        if person.get("id") == "quality_acc":
            return True
        name = (person.get("name") or "").strip()
        return name.startswith("품질팀") or "품질팀" in name

    def _is_tech_support_person(person=None):
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return False
        if person.get("id") in ("tech_acc", "rods_lab"):
            return True
        name = (person.get("name") or "").strip()
        return (
            name.startswith("기술부")
            or "기술부 계정" in name
            or name.startswith("기술연구소")
            or "기술연구소" in name
        )

    def _support_request_kind(person=None):
        """field_support 계정 구분: quality / tech / all."""
        if _is_quality_support_person(person):
            return "quality"
        if _is_tech_support_person(person):
            return "tech"
        if person and person.get("team_code") == "field_support":
            return "all"
        return ""

    def _receipt_matches_support(receipt, kind):
        if not receipt:
            return False
        if kind == "quality":
            return bool(receipt.get("support_quality"))
        if kind == "tech":
            return bool(receipt.get("support_tech"))
        if kind == "all":
            return bool(receipt.get("support_quality") or receipt.get("support_tech"))
        return True

    def _filter_rows_for_field_support(rows, person):
        """6.4: 품질팀/기술부 로그인 시 지원요청 건만 노출."""
        if not person or person.get("team_code") != "field_support":
            return rows
        kind = _support_request_kind(person)
        return [r for r in rows if _receipt_matches_support(r, kind)]

    def _is_sales_head(person=None):
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return False
        if getattr(app_module, "_is_exec_ops_privileged", None):
            if app_module._is_exec_ops_privileged(person):
                return True
        if person.get("id") == "sales_head" or person.get("team_code") == "sales_head":
            return True
        name = (person.get("name") or "")
        return "영업부 부서장" in name or name == "영업부 부서장"

    def _is_export_self(person=None):
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return False
        if person.get("id") == app_module.EXPORT_AS_SELF_CODE:
            return True
        name = (person.get("name") or "").strip()
        return name == app_module.EXPORT_AS_SELF_NAME or name.startswith(
            app_module.EXPORT_AS_SELF_NAME
        )

    def _is_receipt_creator(receipt, person=None):
        if not receipt or not person:
            return False
        if receipt.get("receipt_staff") == person.get("id"):
            return True
        if receipt.get("created_by_username") and person.get("name"):
            if receipt["created_by_username"].casefold() == person["name"].casefold():
                return True
        staff_name = (receipt.get("receipt_staff_name") or "").strip()
        return bool(staff_name and staff_name.startswith(person.get("name") or ""))

    def _can_access_sales(person=None):
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return True  # 미매칭 계정은 기존 동작 유지
        if app_module._is_tech_mgmt_person(person):
            return False
        # 부서장: 영업접수·이력관리 모두 가능 (상단 메뉴 노출)
        if _is_sales_head(person):
            return True
        if app_module._is_executive_person(person):
            return False
        return bool(person.get("can_receipt"))

    def _can_access_history(person=None):
        try:
            if session.get("user_role") == "admin":
                return True
        except RuntimeError:
            pass
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return True
        if app_module._is_tech_mgmt_person(person):
            return False
        # 영업부 부서장: 영업접수와 동일하게 이력관리 메뉴·접근 허용
        if _is_sales_head(person):
            return True
        if app_module._is_executive_person(person):
            return False
        # 지원부서(field_support)는 지원요청 건 처리용으로 이력 허용
        if person.get("team_code") == "field_support":
            return True
        return bool(person.get("can_as"))

    def _can_process_export_as(receipt, person=None):
        if not receipt or receipt.get("team_id") != "export":
            return True
        if person is None:
            person = app_module._get_person_for_session()
        if app_module._is_export_as_handler(person):
            return True
        # 김윤식: 수출 건 처리 가능(이명지 접수 포함). 최영호·박용수는 불가.
        if _is_export_self(person):
            return True
        return False

    def _can_see_export_history(person=None):
        if app_module._is_export_as_handler(person):
            return True
        if person is None:
            person = app_module._get_person_for_session()
        if not person:
            return False
        if person.get("team_code") == "export":
            return True
        return False

    def _waiting_receipts(tab="domestic", person=None):
        if person is None:
            person = app_module._get_person_for_session()
        # 6.4 지원부서: 품질/기술 지원요청 건만
        if person and person.get("team_code") == "field_support":
            kind = _support_request_kind(person)
            items = []
            for receipt in _scoped_receipts():
                if not app_module._is_waiting(receipt):
                    continue
                if not _receipt_matches_support(receipt, kind):
                    continue
                if not app_module._match_history_tab(receipt, tab):
                    continue
                items.append(receipt)
            items.sort(key=lambda x: x.get("created_at") or "")
            return items

        items = []
        for receipt in _scoped_receipts():
            if not app_module._is_waiting(receipt):
                continue
            if not app_module._match_history_tab(receipt, tab):
                continue
            if receipt.get("team_id") == "export":
                # 장호빈: 내수탭 포함 전체
                if app_module._is_export_as_handler(person):
                    if tab == "domestic" or tab == "export":
                        items.append(receipt)
                    continue
                # 김윤식: 수출 탭 전체(이명지 접수 포함)
                if _is_export_self(person):
                    if tab == "export":
                        items.append(receipt)
                    continue
                # 기타 수출팀(이명지 등 can_as 없으면 메뉴 숨김이지만 방어)
                if person and person.get("team_code") == "export":
                    if tab == "export":
                        items.append(receipt)
                    continue
                continue
            if app_module._is_jang_hb(person) and not app_module._jang_hb_assigned(
                receipt, person
            ):
                continue
            items.append(receipt)
        if app_module._is_jang_hb(person):
            items = [
                r for r in items if app_module._jang_hb_assigned(r, person)
            ]
        # 영업접수 목록과 동일하게 FIFO(오래된 접수가 위)
        items.sort(key=lambda x: x.get("created_at") or "")
        return items

    def _assign_export_as_handler(receipt, *, default_if_empty=False):
        """수출: 장호빈·김윤식·미지정만 허용. 신규 시 기본 장호빈."""
        if not receipt or receipt.get("team_id") != "export":
            return
        as_id = (receipt.get("as_staff") or "").strip()
        if not as_id:
            if default_if_empty:
                receipt["as_staff"] = app_module.EXPORT_AS_HANDLER_CODE
                receipt["as_staff_name"] = app_module._export_as_staff_label(
                    app_module.EXPORT_AS_HANDLER_CODE
                )
            else:
                receipt["as_staff"] = ""
                receipt["as_staff_name"] = ""
            return
        if as_id in app_module.EXPORT_AS_ALLOWED_CODES:
            receipt["as_staff"] = as_id
            receipt["as_staff_name"] = app_module._export_as_staff_label(as_id)
            return
        receipt["as_staff"] = app_module.EXPORT_AS_HANDLER_CODE
        receipt["as_staff_name"] = app_module._export_as_staff_label(
            app_module.EXPORT_AS_HANDLER_CODE
        )

    def _team_scope_codes(team_code, person=None):
        if team_code == "executive" or app_module._is_tech_mgmt_team_code(team_code):
            return None
        if team_code == "sales_head" or _is_sales_head(person):
            return None  # 전사
        if team_code == "field_support":
            # 지원부서: 전 팀 접수 중 지원요청 건만 (필터는 _filter_rows_for_field_support)
            return None
        if team_code == "domestic":
            # 내수 팀원: 자팀 + (팀리더 가시성) 장호빈이 맡은 수출건도 홈에 표시
            # 스펙 6.3.2: 최영호도 팀 AS 목록에서 수출→장호빈 건 확인
            return {"domestic", "export"}
        if team_code:
            return {team_code}
        return set()

    def _filter_team_rows_for_domestic(rows, person):
        """내수 팀원: 타팀(수출)에서 자팀 AS로 라우팅된 건도 팀 목록에 표시(6.3.2)."""
        if not person or person.get("team_code") != "domestic":
            return rows
        if app_module._is_export_as_handler(person):
            return rows
        domestic_as_ids = {
            p["id"]
            for p in app_module._get_org_people()
            if p.get("team_code") == "domestic" and p.get("can_as")
        }
        domestic_as_ids.add(app_module.EXPORT_AS_HANDLER_CODE)
        filtered = []
        for r in rows:
            if r.get("team_id") != "export":
                filtered.append(r)
                continue
            as_id = (r.get("as_staff") or "").strip()
            # 미지정(내수 라우팅) 또는 내수 AS 담당자 지정 건
            if not as_id or as_id in domestic_as_ids:
                filtered.append(r)
        return filtered

    def _push_notification(
        *,
        user_code="",
        team_code="",
        title="",
        body="",
        link="",
        receipt_no="",
    ):
        now = datetime.now().isoformat(timespec="seconds")
        with app_module._user_db() as conn:
            conn.execute(
                """
                INSERT INTO notifications
                (user_code, team_code, title, body, link, receipt_no, is_read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    user_code or "",
                    team_code or "",
                    title,
                    body,
                    link,
                    receipt_no or "",
                    now,
                ),
            )

    def _notifications_for_person(person, limit=30):
        if not person:
            return []
        code = person.get("id") or ""
        team = person.get("team_code") or ""
        with app_module._user_db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM notifications
                WHERE (user_code != '' AND user_code = ?)
                   OR (user_code = '' AND team_code != '' AND team_code IN (?, ?))
                ORDER BY id DESC
                LIMIT ?
                """,
                (code, team, code, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def _unread_notification_count(person):
        if not person:
            return 0
        code = person.get("id") or ""
        team = person.get("team_code") or ""
        with app_module._user_db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM notifications
                WHERE is_read = 0 AND (
                    (user_code != '' AND user_code = ?)
                    OR (user_code = '' AND team_code != '' AND team_code IN (?, ?))
                )
                """,
                (code, team, code),
            ).fetchone()
        return int(row["c"] if row else 0)

    def _notify_export_register(receipt):
        """접수 등록 알림(지정/미지정)."""
        as_id = (receipt.get("as_staff") or "").strip()
        no = receipt.get("receipt_no") or ""
        cust = receipt.get("customer_name") or ""
        equip = receipt.get("equipment_name") or ""
        if as_id:
            _push_notification(
                user_code=as_id,
                title=f"[AS접수] {no} 신규 접수 - {cust} ({equip})",
                body=f"증상: {(receipt.get('symptoms') or '')[:120]}",
                link="/history",
                receipt_no=no,
            )
            return
        team = receipt.get("team_id") or ""
        if team == "export":
            for code in app_module.EXPORT_AS_ALLOWED_CODES:
                _push_notification(
                    user_code=code,
                    title=f"[AS접수] (미지정) 신규 접수 - {cust}",
                    body="AS 대응팀 대기열에 등록되었습니다.",
                    link="/history",
                    receipt_no=no,
                )
            return
        target_team = team or "domestic"
        _push_notification(
            team_code=target_team,
            title=f"[AS접수] (미지정) 신규 접수 - {cust}",
            body="AS 대응팀 대기열에 등록되었습니다.",
            link="/history",
            receipt_no=no,
        )

    def _build_auto_detect_cards(card_limit=None):
        """6.12: 규칙 기반 검토 필요 후보 (강제 에스컬레이션 없음).

        card_limit이 있으면 홈 카드용으로 상위 N건만 구성하고,
        정확도 통계(reviewed_levels)는 전체 기준으로 계산한다.
        """
        import heapq

        flagged = []
        top_heap = []  # (score, seq, card) — 상위 card_limit 유지
        seq = 0
        cleared_count = 0
        reviewed_levels = []
        try:
            source = app_module._line_receipts()
        except Exception:
            source = app_module.RECEIPTS
        indexes = v32.build_auto_detect_indexes(source)
        for r in source:
            result = v32.auto_detect_result(
                r,
                source,
                app_module.REPORT_DETAILS,
                indexes=indexes,
            )
            flags = result["flags"]
            if not flags:
                continue
            esc = v32.normalize_escalation(r)
            # 정확도용: 감지된 적 있는 건의 현재 등급
            reviewed_levels.append(esc)
            if esc == "cleared":
                cleared_count += 1
                continue
            if esc in ("monitor", "critical", "closed"):
                # 이미 정식 등급 지정됨 → 후보 카드에서 제외
                continue
            # normal 등: 검토 필요 후보로만 노출 (등급 자동 변경 없음)
            days = _days_open(r)
            card = {
                **r,
                "auto_flags": flags,
                "auto_flag_details": result.get("details") or {},
                "auto_flag_labels": result.get("labels")
                or [v32.FLAG_LABELS.get(f, f) for f in flags],
                "escalation_level": esc,
                "escalation_label": dict(v32.ESCALATION_OPTIONS).get(esc, esc),
                "days_open": days,
                "status_label": v32.status_label(r.get("status")),
            }
            if card_limit is None:
                flagged.append(card)
            elif card_limit > 0:
                score = (len(flags), days or 0)
                seq += 1
                item = (score, seq, card)
                if len(top_heap) < card_limit:
                    heapq.heappush(top_heap, item)
                elif score > top_heap[0][0]:
                    heapq.heapreplace(top_heap, item)
        if card_limit is None:
            # 사유 많은 건 · 장기미해결 우선
            flagged.sort(
                key=lambda x: (
                    -len(x.get("auto_flags") or []),
                    -(x.get("days_open") or 0),
                    x.get("receipt_no") or "",
                )
            )
        else:
            flagged = [
                card
                for _, _, card in sorted(
                    top_heap, key=lambda x: (-x[0][0], -x[0][1], x[1])
                )
            ]
        accuracy = v32.auto_detect_accuracy(reviewed_levels)
        return flagged, cleared_count, accuracy

    def _days_open(r):
        rd = v32.parse_date(r.get("receipt_date"))
        if not rd:
            return None
        from datetime import date as _date
        return (_date.today() - rd).days

    # --- monkey-patch core helpers ---
    app_module._is_sales_head = _is_sales_head
    app_module._is_export_self = _is_export_self
    app_module._can_access_sales = _can_access_sales
    app_module._can_access_history = _can_access_history
    app_module._can_process_export_as = _can_process_export_as
    app_module._can_see_export_history = _can_see_export_history
    app_module._waiting_receipts = _waiting_receipts
    app_module._assign_export_as_handler = _assign_export_as_handler
    app_module._team_scope_codes = _team_scope_codes
    app_module._filter_team_rows_for_domestic = _filter_team_rows_for_domestic
    app_module._filter_rows_for_field_support = _filter_rows_for_field_support
    app_module._is_quality_support_person = _is_quality_support_person
    app_module._is_tech_support_person = _is_tech_support_person
    app_module._support_request_kind = _support_request_kind
    app_module._push_notification = _push_notification
    app_module._notifications_for_person = _notifications_for_person
    app_module._unread_notification_count = _unread_notification_count
    app_module._notify_export_register = _notify_export_register
    app_module._build_auto_detect_cards = _build_auto_detect_cards

    # --- override _is_waiting / enrich status ---
    def _is_waiting(receipt):
        return v32.is_pending(receipt.get("status"))

    app_module._is_waiting = _is_waiting
    app_module._is_pending_receipt = lambda r: v32.is_pending(r.get("status"))

    orig_get_queue = app_module._get_queue_info

    def _get_queue_info(receipt, all_receipts):
        return v32.queue_info(receipt, _scoped_receipts(all_receipts, receipt=receipt))

    app_module._get_queue_info = _get_queue_info

    orig_enrich = app_module._enrich_receipt

    def _enrich_receipt(receipt, all_receipts, *, skip_queue=False, queue_team_scope=None):
        base = orig_enrich(
            receipt,
            all_receipts,
            skip_queue=skip_queue,
            queue_team_scope=queue_team_scope,
        )
        base["status_label"] = v32.status_label(receipt.get("status"))
        st = receipt.get("status")
        if st in ("parts_waiting", "on_hold", "draft"):
            base["filter_status"] = "in_progress"
        else:
            base["filter_status"] = st
        # origin tag helper
        base["origin_tag"] = ""
        if receipt.get("team_id") == "export":
            base["origin_tag"] = "[수출영업팀접수]"
        esc = v32.normalize_escalation(receipt)
        base["escalation_level"] = esc
        base["escalation_label"] = dict(v32.ESCALATION_OPTIONS).get(esc, esc)
        return base

    app_module._enrich_receipt = _enrich_receipt

    # --- routes ---
    @app.route("/api/past-history")
    @app_module._login_required
    def api_past_history():
        customer = request.args.get("customer", "").strip()
        equipment = request.args.get("equipment", "").strip()
        exclude = request.args.get("exclude", "").strip()
        data = v32.past_history(customer, equipment, _scoped_receipts(), exclude)
        return jsonify(data)

    @app.route("/api/re-repair-suggest")
    @app_module._login_required
    def api_re_repair_suggest():
        return jsonify(
            v32.suggest_re_repair(
                request.args.get("customer", ""),
                request.args.get("equipment", ""),
                request.args.get("serial", ""),
                _scoped_receipts(),
                app_module.REPORT_DETAILS,
                request.args.get("exclude", ""),
            )
        )

    @app.route("/api/warranty")
    @app_module._login_required
    def api_warranty():
        return jsonify(
            v32.warranty_verdict(
                request.args.get("ship_date", ""),
                request.args.get("inspect_date", ""),
            )
        )

    @app.route("/api/notifications")
    @app_module._login_required
    def api_notifications():
        person = app_module._get_person_for_session()
        items = _notifications_for_person(person)
        return jsonify(
            {
                "unread": _unread_notification_count(person),
                "items": [
                    {
                        "id": n["id"],
                        "title": n["title"],
                        "body": n["body"],
                        "link": n["link"],
                        "receipt_no": n["receipt_no"],
                        "is_read": bool(n["is_read"]),
                        "created_at": n["created_at"],
                    }
                    for n in items
                ],
            }
        )

    @app.route("/api/notifications/<int:nid>/read", methods=["POST"])
    @app_module._login_required
    def api_notification_read(nid):
        with app_module._user_db() as conn:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (nid,))
        return jsonify({"ok": True})

    @app.route("/api/notifications/read-all", methods=["POST"])
    @app_module._login_required
    def api_notification_read_all():
        person = app_module._get_person_for_session()
        if not person:
            return jsonify({"ok": False}), 400
        code = person.get("id") or ""
        team = person.get("team_code") or ""
        with app_module._user_db() as conn:
            conn.execute(
                """
                UPDATE notifications SET is_read = 1
                WHERE is_read = 0 AND (
                    (user_code != '' AND user_code = ?)
                    OR (user_code = '' AND team_code != '' AND team_code IN (?, ?))
                )
                """,
                (code, team, code),
            )
        return jsonify({"ok": True})

    @app.route("/api/sales/billing", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_sales_billing():
        person = app_module._get_person_for_session()
        receipt_no = request.form.get("receipt_no", "").strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not _can_edit_sales_ops(receipt, person):
            return jsonify({"error": "권한 없음"}), 403
        mode = request.form.get("mode", "margin")
        detail = app_module.REPORT_DETAILS.setdefault(receipt_no, {})
        parts_total = v32.calc_parts_total(detail.get("parts"))
        if mode == "manual":
            try:
                amount = int(str(request.form.get("amount", "0")).replace(",", ""))
            except ValueError:
                amount = 0
            receipt["billing_cost"] = amount
            receipt["billing_manual"] = 1
            receipt["billing_set_by"] = (person or {}).get("name") or session.get("user_name") or ""
        else:
            try:
                rate = float(request.form.get("margin_rate") or v32.DEFAULT_MARGIN_RATE)
            except ValueError:
                rate = v32.DEFAULT_MARGIN_RATE
            result = v32.calc_billing_from_margin(parts_total, rate)
            receipt["billing_cost"] = result["billing_cost"]
            receipt["billing_margin_rate"] = str(rate)
            receipt["billing_manual"] = 0
            receipt["billing_set_by"] = (person or {}).get("name") or ""
        if receipt.get("charge_type") != "paid":
            receipt["charge_type"] = "paid"
        app_module._save_service_data()
        return jsonify(
            {
                "ok": True,
                "billing_cost": receipt.get("billing_cost"),
                "billing_display": f"{int(receipt['billing_cost']):,}원",
            }
        )

    @app.route("/api/sales/charge-type", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_sales_charge_type():
        person = app_module._get_person_for_session()
        receipt_no = request.form.get("receipt_no", "").strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not _can_edit_sales_ops(receipt, person):
            return jsonify({"error": "권한 없음"}), 403
        new_type = request.form.get("charge_type", "free")
        if new_type not in ("free", "paid"):
            return jsonify({"error": "잘못된 값"}), 400
        receipt["charge_type"] = new_type
        if new_type == "free":
            # 6.5: 유상→무상 전환 시 청구비용 초기화
            app_module._clear_billing_on_free(receipt)
        app_module._save_service_data()
        return jsonify({"ok": True, "charge_type": new_type})

    @app.route("/api/sales/parts", methods=["GET", "POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_sales_parts():
        person = app_module._get_person_for_session()
        receipt_no = (
            request.args.get("receipt_no") or request.form.get("receipt_no") or ""
        ).strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not app_module._can_view_sales_receipt(receipt, person) and not _can_edit_sales_ops(
            receipt, person
        ):
            return jsonify({"error": "권한 없음"}), 403
        detail = app_module.REPORT_DETAILS.setdefault(receipt_no, {})
        if request.method == "GET":
            return jsonify(
                {
                    "parts": detail.get("parts") or [],
                    "charge_type": receipt.get("charge_type"),
                    "billing_manual": int(receipt.get("billing_manual") or 0),
                    "billing_cost": receipt.get("billing_cost"),
                    "billing_margin_rate": str(
                        app_module._effective_margin_rate(
                            receipt.get("billing_margin_rate")
                        )
                    ),
                }
            )
        if not _can_edit_sales_ops(receipt, person):
            return jsonify({"error": "권한 없음"}), 403
        if (
            app_module._is_receipt_completed(receipt)
            and session.get("user_role") != "admin"
        ):
            return jsonify(
                {
                    "error": "완료된 건의 교체부품(AS기록)은 관리자만 수정할 수 있습니다. 청구금액·마진율은 가격책정을 사용하세요.",
                }
            ), 403
        try:
            parts = json.loads(request.form.get("parts_json", "[]"))
        except json.JSONDecodeError:
            parts = []
        cleaned = []
        for p in parts:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            try:
                qty = float(p.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if float(qty).is_integer():
                qty = int(qty)
            try:
                unit_price = int(str(p.get("unit_price") or "0").replace(",", ""))
            except (TypeError, ValueError):
                unit_price = 0
            cleaned.append(
                {
                    "name": name,
                    "spec": (p.get("spec") or "").strip(),
                    "qty": qty,
                    "unit_price": unit_price,
                    "subtotal": int(round(float(qty) * unit_price)),
                }
            )
        detail["parts"] = cleaned
        parts_total = v32.calc_parts_total(cleaned)
        billing_updated = False
        # 6.6.1: 유상 + 미수동책정(마진모드)만 청구비용 재계산. 수동책정은 유지.
        if receipt.get("charge_type") == "paid" and not int(
            receipt.get("billing_manual") or 0
        ):
            rate = receipt.get("billing_margin_rate")
            try:
                rate_f = float(rate) if rate not in (None, "") else None
            except ValueError:
                rate_f = None
            result = v32.calc_billing_from_margin(parts_total, rate_f)
            receipt["billing_cost"] = result["billing_cost"]
            receipt["billing_margin_rate"] = str(result["margin_rate"])
            billing_updated = True
        app_module._save_service_data()
        return jsonify(
            {
                "ok": True,
                "parts": cleaned,
                "parts_total": parts_total,
                "billing_updated": billing_updated,
                "billing_cost": receipt.get("billing_cost"),
                "billing_manual": int(receipt.get("billing_manual") or 0),
            }
        )

    @app.route("/api/sales/assign-as", methods=["POST"])
    @app_module._login_required
    @app_module._service_write_locked
    def api_sales_assign_as():
        person = app_module._get_person_for_session()
        receipt_no = request.form.get("receipt_no", "").strip()
        as_staff_id = request.form.get("as_staff", "").strip()
        receipt = app_module._get_receipt_by_no(receipt_no)
        if not receipt:
            return jsonify({"error": "접수 없음"}), 404
        if not app_module._can_reassign_as_staff(receipt, person):
            return jsonify({"error": "권한 없음"}), 403
        if (
            app_module._is_receipt_completed(receipt)
            and session.get("user_role") != "admin"
        ):
            return jsonify({"error": "완료된 건의 AS 담당자는 관리자만 변경할 수 있습니다."}), 403
        old = receipt.get("as_staff", "")
        receipt["as_staff"] = as_staff_id
        receipt["as_staff_name"] = (
            app_module._staff_name("as_staff", as_staff_id) if as_staff_id else ""
        )
        if receipt.get("team_id") == "export":
            if not (
                _is_sales_head(person) or session.get("user_role") == "admin"
            ):
                app_module._assign_export_as_handler(receipt)
            elif not as_staff_id:
                receipt["as_staff"] = ""
                receipt["as_staff_name"] = ""
            else:
                receipt["as_staff_name"] = (
                    app_module._staff_name("as_staff", as_staff_id) or ""
                )
        app_module._save_service_data()
        if receipt.get("as_staff") and receipt.get("as_staff") != old:
            app_module._notify_as_staff_reassigned(receipt, receipt["as_staff"])
        return jsonify({"ok": True, "as_staff": receipt.get("as_staff")})

    def _can_edit_sales_ops(receipt, person):
        return app_module._can_edit_billing_ops(receipt, person)

    app_module._can_edit_sales_ops = _can_edit_sales_ops

    # 6.11 최고기술경영실 조회용 API (그룹사 연동 대비)
    @app.route("/api/tech/escalations")
    @app_module._login_required
    def api_tech_escalations():
        person = app_module._get_person_for_session()
        if not (
            app_module._is_tech_mgmt_person(person)
            or app_module._is_executive_person(person)
            or session.get("user_role") == "admin"
        ):
            return jsonify({"error": "권한 없음"}), 403
        level = (request.args.get("level") or "active").strip()
        items = []
        for r in _scoped_receipts():
            esc = v32.normalize_escalation(r)
            if level == "active" and esc not in ("monitor", "critical"):
                continue
            if level not in ("", "all", "active") and esc != level:
                continue
            items.append(
                {
                    "receipt_no": r.get("receipt_no"),
                    "receipt_date": r.get("receipt_date"),
                    "team_id": r.get("team_id"),
                    "team": r.get("team"),
                    "customer_name": r.get("customer_name"),
                    "equipment_name": r.get("equipment_name"),
                    "status": r.get("status"),
                    "status_label": v32.status_label(r.get("status")),
                    "escalation_level": esc,
                    "escalation_label": dict(v32.ESCALATION_OPTIONS).get(esc, esc),
                    "intervention_status": r.get("intervention_status") or "none",
                    "compensation_status": r.get("compensation_status") or "none",
                    "compensation_amount": r.get("compensation_amount") or "",
                    "as_staff": r.get("as_staff") or "",
                    "as_staff_name": r.get("as_staff_name") or "",
                }
            )
        items.sort(key=lambda x: x.get("receipt_date") or "", reverse=True)
        return jsonify(
            {
                "system": "HD Hyundai Power & System — Welding AS",
                "count": len(items),
                "items": items,
            }
        )

    return True
