"""회사 SMTP를 이용한 담당자 이메일 알림."""

import html
import logging
import os
import re
import smtplib
import socket
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)
_MAIL_CONFIG_DIR = None


def load_mail_config(base_dir, force=False):
    """mail.env 또는 환경변수에서 SMTP 설정을 읽는다."""
    global _MAIL_CONFIG_DIR
    _MAIL_CONFIG_DIR = Path(base_dir)
    env_path = _MAIL_CONFIG_DIR / "mail.env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (force or key not in os.environ):
            os.environ[key] = value


def _reload_mail_config():
    if _MAIL_CONFIG_DIR:
        load_mail_config(_MAIL_CONFIG_DIR, force=True)


def mail_configured():
    return bool(
        os.environ.get("MAIL_SERVER")
        and os.environ.get("MAIL_USERNAME")
        and os.environ.get("MAIL_PASSWORD")
    )


def mail_error_message(code):
    messages = {
        "mail_not_configured": "메일 설정(mail.env)이 없습니다.",
        "no_recipients": "접수·AS 담당자 이메일이 등록되어 있지 않습니다.",
        "auth_failed": (
            "SMTP 인증 실패입니다. 네이버웍스(Works Mobile)는 로그인 비밀번호가 아니라 "
            "「외부 앱 비밀번호」를 MAIL_PASSWORD에 넣어야 합니다. "
            "또한 Admin에서 해당 계정의 IMAP/SMTP 사용 권한을 확인해 주세요."
        ),
        "connection_failed": (
            "SMTP 서버에 연결하지 못했습니다. "
            "서버 인터넷/DNS, MAIL_SERVER, MAIL_PORT(587 또는 465)를 확인해 주세요."
        ),
        "dns_failed": (
            "SMTP 서버 주소를 찾을 수 없습니다(DNS 오류). "
            "MAIL_SERVER 값과 서버 DNS/인터넷 연결을 확인해 주세요."
        ),
        "send_failed": "이메일 발송에 실패했습니다. mail.env 설정을 확인해 주세요.",
    }
    return messages.get(code, messages["send_failed"])


def _mail_bool(name, default=False):
    value = (os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def _smtp_send(msg, username, password, recipients):
    server_host = os.environ["MAIL_SERVER"].strip()
    port = int(os.environ.get("MAIL_PORT", "587"))
    use_ssl = _mail_bool("MAIL_USE_SSL", port == 465)
    use_tls = _mail_bool("MAIL_USE_TLS", not use_ssl)
    timeout = int(os.environ.get("MAIL_TIMEOUT", "30"))
    retries = max(1, int(os.environ.get("MAIL_RETRIES", "3")))

    def _connect_and_send(host, smtp_port, ssl_mode, tls_mode):
        if ssl_mode:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, smtp_port, timeout=timeout, context=context) as smtp:
                smtp.login(username, password)
                smtp.sendmail(username, recipients, msg.as_string())
            return
        with smtplib.SMTP(host, smtp_port, timeout=timeout) as smtp:
            smtp.ehlo()
            if tls_mode:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(username, password)
            smtp.sendmail(username, recipients, msg.as_string())

    last_err = None
    for attempt in range(retries):
        try:
            _connect_and_send(server_host, port, use_ssl, use_tls)
            return
        except socket.gaierror as exc:
            last_err = exc
            logger.warning(
                "SMTP DNS 조회 실패 (%s/%s): %s",
                attempt + 1,
                retries,
                server_host,
            )
            if attempt + 1 < retries:
                time.sleep(0.8 * (attempt + 1))
        except (smtplib.SMTPException, TimeoutError) as exc:
            if not use_ssl and port != 465:
                try:
                    _connect_and_send(server_host, 465, True, False)
                    return
                except Exception as fallback_exc:
                    last_err = fallback_exc
                    break
            last_err = exc
            break
        except OSError as exc:
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(0.8 * (attempt + 1))
            else:
                break

    if last_err:
        raise last_err


def send_email(to_addrs, subject, body_html):
    """이메일 발송. (성공 여부, 오류 코드) 반환."""
    _reload_mail_config()
    if not mail_configured():
        logger.info("메일 설정 없음: 발송 생략")
        return False, "mail_not_configured"

    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    recipients = [addr.strip() for addr in to_addrs if addr and addr.strip()]
    if not recipients:
        return False, "no_recipients"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM") or os.environ.get("MAIL_USERNAME")
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    username = os.environ["MAIL_USERNAME"].strip()
    password = os.environ["MAIL_PASSWORD"]

    try:
        _smtp_send(msg, username, password, recipients)
        logger.info("메일 발송 완료: %s -> %s", subject, recipients)
        return True, None
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP 인증 실패: %s", subject)
        return False, "auth_failed"
    except socket.gaierror:
        logger.exception("SMTP DNS 조회 실패: %s", subject)
        return False, "dns_failed"
    except (smtplib.SMTPException, OSError, TimeoutError):
        logger.exception("SMTP 연결/발송 실패: %s", subject)
        return False, "connection_failed"
    except Exception:
        logger.exception("메일 발송 실패: %s", subject)
        return False, "send_failed"


def _link(base_url, path):
    base = (base_url or "").rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}" if base else path


def _esc(value):
    text = "" if value is None else str(value)
    return html.escape(text, quote=True)


def _status_label(status):
    labels = {
        "received": "접수",
        "draft": "임시저장",
        "in_progress": "진행중",
        "parts_waiting": "부품대기",
        "on_hold": "보류",
        "completed": "완료",
    }
    return labels.get(status, status or "—")


def _charge_label(charge_type):
    if charge_type == "paid":
        return "유상"
    if charge_type == "free":
        return "무상"
    return charge_type or "—"


def _build_mail_html(title, rows, links=None, note=None):
    """표 형식의 깔끔한 HTML 메일 본문."""
    row_html = []
    for i, (label, value) in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#faf8f6"
        display = _esc(value) if (value is not None and str(value).strip() != "") else "—"
        row_html.append(
            f"""
      <tr>
        <td style="width:132px;padding:10px 14px;background:#f3eee8;border:1px solid #e2d8ce;
                   color:#5c4a3d;font-weight:600;font-size:13px;vertical-align:top;white-space:nowrap;">
          {_esc(label)}
        </td>
        <td style="padding:10px 14px;background:{bg};border:1px solid #e2d8ce;
                   color:#1f2937;font-size:13px;line-height:1.5;word-break:break-word;">
          {display}
        </td>
      </tr>"""
        )

    link_parts = []
    for text, url in links or []:
        if not url:
            continue
        link_parts.append(
            f'<a href="{_esc(url)}" style="display:inline-block;margin:0 6px 6px 0;padding:8px 14px;'
            f'background:#7a1f1a;color:#ffffff;text-decoration:none;border-radius:4px;'
            f'font-size:13px;font-weight:600;">{_esc(text)}</a>'
        )
    links_block = ""
    if link_parts:
        links_block = (
            '<div style="margin-top:18px;padding-top:4px;">'
            + "".join(link_parts)
            + "</div>"
        )

    note_block = ""
    if note:
        note_block = (
            f'<p style="margin:14px 0 0;color:#6b7280;font-size:12px;line-height:1.5;">'
            f"{_esc(note)}</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:'Malgun Gothic','Apple SD Gothic Neo',Arial,sans-serif;">
  <div style="max-width:640px;margin:24px auto;padding:0 12px;">
    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
      <div style="background:#7a1f1a;padding:16px 20px;">
        <div style="color:#ffffff;font-size:11px;letter-spacing:0.04em;opacity:0.85;">HD Hyundai Power &amp; System · AS</div>
        <h1 style="margin:6px 0 0;color:#ffffff;font-size:18px;font-weight:700;line-height:1.35;">{_esc(title)}</h1>
      </div>
      <div style="padding:20px;">
        <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
               style="border-collapse:collapse;width:100%;border:1px solid #e2d8ce;">
          {''.join(row_html)}
        </table>
        {links_block}
        {note_block}
      </div>
    </div>
    <p style="margin:12px 8px 0;color:#9ca3af;font-size:11px;text-align:center;">
      본 메일은 AS 시스템에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>"""


def _receipt_detail_rows(receipt, *, extra=None):
    pl = (receipt.get("product_line") or "").strip().lower()
    is_rod = pl in ("rod", "rods")
    rows = [
        ("접수번호", receipt.get("receipt_no") or ""),
        ("접수일", receipt.get("receipt_date") or ""),
        ("AS 대응팀", receipt.get("team") or ""),
        ("고객명", receipt.get("customer_name") or ""),
        ("제품명" if is_rod else "장비명", receipt.get("equipment_name") or ""),
    ]
    if is_rod:
        if receipt.get("spec"):
            rows.append(("규격", receipt.get("spec") or ""))
        if receipt.get("lot"):
            rows.append(("로트번호", receipt.get("lot") or ""))
    rows.extend(
        [
            ("증상", receipt.get("symptoms") or ""),
            ("유/무상", _charge_label(receipt.get("charge_type"))),
            ("접수 담당", receipt.get("receipt_staff_name") or "미지정"),
        ]
    )
    if (receipt.get("office") or "").strip():
        rows.append(("접수사무소", (receipt.get("office") or "").strip()))
    rows.extend(
        [
            ("AS 담당", receipt.get("as_staff_name") or "미지정"),
            ("상태", _status_label(receipt.get("status"))),
        ]
    )
    if extra:
        rows.extend(extra)
    return rows


def _parse_staff_emails(value):
    """담당자 조회 결과(단일·다중 문자열)를 수신자 목록으로 변환."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = re.split(r"[;,\n]+", str(value))
    emails = []
    seen = set()
    for part in parts:
        email = part.strip()
        if not email or "@" not in email:
            continue
        key = email.casefold()
        if key in seen:
            continue
        seen.add(key)
        emails.append(email)
    return emails


def _staff_emails(receipt, get_staff_email):
    emails = []
    seen = set()
    for code in (receipt.get("as_staff"), receipt.get("receipt_staff")):
        for email in _parse_staff_emails(get_staff_email(code)):
            key = email.casefold()
            if key in seen:
                continue
            seen.add(key)
            emails.append(email)
    return emails


def _emails_for_codes(codes, get_staff_email):
    emails = []
    seen = set()
    for code in codes or []:
        for email in _parse_staff_emails(get_staff_email(code)):
            key = email.casefold()
            if key in seen:
                continue
            seen.add(key)
            emails.append(email)
    return emails


def notify_receipt_registered(receipt, get_staff_email, base_url, recipient_codes=None):
    """신규 AS 접수 알림 — 지정 AS 또는 처리팀 AS 가능 인원."""
    if recipient_codes is None:
        as_id = (receipt.get("as_staff") or "").strip()
        recipient_codes = [as_id] if as_id else []
    recipients = _emails_for_codes(recipient_codes, get_staff_email)
    if not recipients:
        return False

    receipt_no = receipt.get("receipt_no", "")
    history_url = _link(
        base_url,
        f"/history?tab={receipt.get('team_id', 'domestic')}&receipt_no={receipt_no}",
    )
    assigned = bool((receipt.get("as_staff") or "").strip())
    subject_prefix = "[AS접수]" if assigned else "[AS접수] (미지정)"
    body = _build_mail_html(
        "신규 AS 접수 알림" if assigned else "미지정 신규 AS 접수 알림",
        _receipt_detail_rows(receipt),
        links=[("이력관리에서 확인", history_url)],
    )
    ok, _ = send_email(
        recipients,
        f"{subject_prefix} {receipt_no} · {receipt.get('customer_name', '')}",
        body,
    )
    return ok


def notify_receipt_completed(receipt, get_staff_email, base_url):
    """AS 완료 알림 → 접수 담당자(개인) 또는 접수사무소 공용 메일."""
    recipients = _parse_staff_emails(get_staff_email(receipt.get("receipt_staff")))
    if not recipients:
        recipients = _parse_staff_emails(get_staff_email(receipt.get("office_team_code")))
    if not recipients:
        return False

    receipt_no = receipt.get("receipt_no", "")
    report_url = _link(base_url, f"/report?receipt_no={receipt_no}")
    extra = [
        ("완료일", receipt.get("completed_date") or ""),
        ("처리자", receipt.get("as_staff_name") or receipt.get("as_technician") or ""),
    ]
    if (receipt.get("charge_type") or "") == "paid":
        extra.append(("청구비용", receipt.get("billing_cost") or "미책정"))
    body = _build_mail_html(
        "AS 처리 완료 알림",
        _receipt_detail_rows(receipt, extra=extra),
        links=[("AS 보고서 보기", report_url)],
    )
    ok, _ = send_email(
        recipients,
        f"[AS완료] {receipt_no} · {receipt.get('customer_name', '')}",
        body,
    )
    return ok


def notify_support_requested(receipt, get_staff_email, base_url, *, target_code, target_label, requester=""):
    recipients = _parse_staff_emails(get_staff_email(target_code))
    if not recipients:
        return False
    receipt_no = receipt.get("receipt_no", "")
    history_url = _link(
        base_url,
        f"/history?tab={receipt.get('team_id', 'domestic')}&receipt_no={receipt_no}",
    )
    body = _build_mail_html(
        f"{target_label} 지원요청",
        _receipt_detail_rows(
            receipt,
            extra=[("요청자", requester or ""), ("요청 부서", target_label)],
        ),
        links=[("이력관리에서 확인", history_url)],
    )
    ok, _ = send_email(
        recipients,
        f"[지원요청] {receipt_no} {target_label} 지원 필요",
        body,
    )
    return ok


def notify_tech_mgmt_reported(receipt, get_staff_email, base_url, *, requester=""):
    recipients = _parse_staff_emails(get_staff_email("tech_mgmt_office"))
    if not recipients:
        return False
    receipt_no = receipt.get("receipt_no", "")
    monitor_url = _link(base_url, f"/tech-monitor?focus={receipt_no}")
    body = _build_mail_html(
        "최고기술경영실 보고",
        _receipt_detail_rows(
            receipt,
            extra=[("요청자", requester or ""), ("현재 상태", _status_label(receipt.get("status")))],
        ),
        links=[("개입·보상에서 확인", monitor_url)],
    )
    ok, _ = send_email(
        recipients,
        f"[지원요청] {receipt_no} 최고기술경영실 보고",
        body,
    )
    return ok


def notify_escalation_changed(receipt, get_staff_email, base_url, *, old_label, new_label):
    code = (receipt.get("as_staff") or receipt.get("receipt_staff") or "").strip()
    recipients = _parse_staff_emails(get_staff_email(code))
    if not recipients:
        return False
    receipt_no = receipt.get("receipt_no", "")
    report_url = _link(base_url, f"/report?receipt_no={receipt_no}")
    body = _build_mail_html(
        "에스컬레이션 등급 변경",
        _receipt_detail_rows(
            receipt,
            extra=[("등급 변경", f"{old_label} → {new_label}")],
        ),
        links=[("AS 보고서 보기", report_url)],
    )
    ok, _ = send_email(
        recipients,
        f"[최고기술경영실] {receipt_no} 등급 변경: {old_label} → {new_label}",
        body,
    )
    return ok


def send_receipt_notice(receipt, get_staff_email, base_url):
    """팀 AS 목록에서 수동 발송하는 AS 안내 메일."""
    if not mail_configured():
        return False, "mail_not_configured"

    recipients = _staff_emails(receipt, get_staff_email)
    if not recipients:
        return False, "no_recipients"

    receipt_no = receipt.get("receipt_no", "")
    history_url = _link(
        base_url,
        f"/history?tab={receipt.get('team_id', 'domestic')}&receipt_no={receipt_no}",
    )
    report_url = _link(base_url, f"/report?receipt_no={receipt_no}")
    body = _build_mail_html(
        "AS 안내",
        _receipt_detail_rows(receipt),
        links=[
            ("이력관리에서 확인", history_url),
            ("AS 보고서 보기", report_url),
        ],
    )
    ok, err = send_email(
        recipients,
        f"[AS 안내] {receipt_no} · {receipt.get('customer_name', '')}",
        body,
    )
    return (True, "sent") if ok else (False, err or "send_failed")


def notify_staff_assigned(receipt, staff_code, role_label, get_staff_email, base_url):
    """담당자 변경 알림."""
    recipients = _parse_staff_emails(get_staff_email(staff_code))
    if not recipients:
        return False

    receipt_no = receipt.get("receipt_no", "")
    sales_url = _link(base_url, f"/sales?edit={receipt_no}#salesForm")
    body = _build_mail_html(
        "AS 담당 배정 알림",
        [
            ("배정 역할", role_label),
            ("접수번호", receipt_no),
            ("접수일", receipt.get("receipt_date") or ""),
            ("AS 대응팀", receipt.get("team") or ""),
            ("고객명", receipt.get("customer_name") or ""),
            (
                "제품명"
                if (receipt.get("product_line") or "").strip().lower() in ("rod", "rods")
                else "장비명",
                receipt.get("equipment_name") or "",
            ),
            ("증상", receipt.get("symptoms") or ""),
            ("유/무상", _charge_label(receipt.get("charge_type"))),
            ("상태", _status_label(receipt.get("status"))),
        ],
        links=[("영업접수에서 확인", sales_url)],
        note=(
            f"[담당자 지정] {(receipt.get('customer_name') or receipt_no or '접수').strip()}건 "
            f"AS담당자로 지정되었습니다"
            if (role_label or "").startswith("AS")
            else f"{role_label}으로 배정되었습니다."
        ),
    )
    ooo = (receipt.get("customer_name") or receipt_no or "접수").strip()
    ok, _ = send_email(
        recipients,
        f"[담당자 지정] {ooo}건 AS담당자로 지정되었습니다"
        if (role_label or "").startswith("AS")
        else f"[AS 배정] {receipt_no} · {role_label}",
        body,
    )
    return ok
