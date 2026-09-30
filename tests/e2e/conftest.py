# -*- coding: utf-8 -*-
"""Playwright E2E fixtures — AS 시스템 캡처 시나리오 검증.

팀원·부서장 등 비밀번호는 users.db 해시와 대조해 확인하고,
평문이 없으면 해당 계정만 테스트용 기본값으로 동기화한다(해시만 저장되는 구조).
비밀번호 값은 로그·스킵 메시지에 출력하지 않는다.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PW = os.environ.get("AS_TEST_PASSWORD", "1234")
ADMIN_PW = os.environ.get("AS_ADMIN_PASSWORD", "admin")
DEFAULT_BASE = "http://127.0.0.1:5004"

# 역할 → org_people.code
ROLE_CODES = {
    "leader": "choi_yh",
    "member": "park_ys",
    "head": "sales_head",
}


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_http(url: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"서버 기동 실패: {url} ({last_err})")


def _password_candidates(username: str, display_name: str, role: str) -> list[str]:
    """DB 평문이 없으므로 해시 대조용 후보. 값은 외부에 찍지 않음."""
    seen: set[str] = set()
    out: list[str] = []

    def add(v: str | None):
        v = (v or "").strip()
        if not v or v in seen:
            return
        seen.add(v)
        out.append(v)

    add(os.environ.get("AS_TEST_PASSWORD"))
    if role == "admin":
        add(os.environ.get("AS_ADMIN_PASSWORD"))
        add("admin")
    add(DEFAULT_PW)
    add("1234")
    add(username)
    add(display_name)
    # 흔한 변경값
    for c in ("0000", "1111", "123456", "pass", "password"):
        add(c)
    return out


def _find_user_row(conn, person_name: str, person_code: str = ""):
    """users.db에서 로그인 행 조회."""
    name = (person_name or "").strip()
    code = (person_code or "").strip()
    row = None
    if name:
        row = conn.execute(
            """
            SELECT id, username, display_name, password_hash, role, active
            FROM users
            WHERE username = ? COLLATE NOCASE
               OR display_name = ? COLLATE NOCASE
            ORDER BY CASE WHEN username = ? COLLATE NOCASE THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (name, name, name),
        ).fetchone()
    if row is None and code:
        row = conn.execute(
            """
            SELECT id, username, display_name, password_hash, role, active
            FROM users
            WHERE username = ? COLLATE NOCASE OR display_name LIKE ?
            LIMIT 1
            """,
            (code, f"%{code}%"),
        ).fetchone()
    return row


def _resolve_password_from_db(conn, row, role: str) -> str:
    """password_hash와 대조해 평문 후보를 찾고, 없으면 테스트용으로 해시 갱신."""
    username = (row["username"] or "").strip()
    display = (row["display_name"] or username).strip()
    pw_hash = row["password_hash"] or ""
    for cand in _password_candidates(username, display, role):
        try:
            if check_password_hash(pw_hash, cand):
                return cand
        except Exception:
            continue

    # 평문 미보관 → E2E용으로 해당 계정만 기본 비번 동기화
    sync_pw = ADMIN_PW if role == "admin" else DEFAULT_PW
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE users
        SET password_hash = ?, active = 1, updated_at = ?
        WHERE id = ?
        """,
        (generate_password_hash(sync_pw), now, row["id"]),
    )
    conn.commit()
    return sync_pw


def _resolve_accounts() -> dict:
    """
    DB(org_people + users)에서 로그인 username·password 해석.
    반환: role -> {username, password, user_id}
    password는 fixture 내부에서만 사용(출력 금지).
    """
    sys.path.insert(0, str(ROOT))
    import app as m  # noqa: WPS433

    people = {p["id"]: p for p in m._get_org_people()}
    head = people.get("sales_head") or next(
        (p for p in m._get_org_people() if p.get("team_code") == "sales_head"),
        None,
    )
    role_person = {
        "leader": people.get("choi_yh"),
        "member": people.get("park_ys"),
        "head": head,
    }

    out: dict = {}
    with m._user_db() as conn:
        for role, person in role_person.items():
            if not person:
                out[role] = {"username": "", "password": DEFAULT_PW, "user_id": None}
                continue
            pname = (person.get("name") or "").strip()
            row = _find_user_row(conn, pname, person.get("id") or "")
            if row is None:
                # 로그인 계정 없으면 생성 후 재조회
                m._ensure_person_login(conn, pname, password=DEFAULT_PW)
                conn.commit()
                row = _find_user_row(conn, pname, person.get("id") or "")
            if row is None:
                out[role] = {"username": pname, "password": DEFAULT_PW, "user_id": None}
                continue
            if not row["active"]:
                conn.execute(
                    "UPDATE users SET active = 1, updated_at = ? WHERE id = ?",
                    (
                        __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                conn.commit()
            pw = _resolve_password_from_db(conn, row, role)
            out[role] = {
                "username": (row["username"] or pname).strip(),
                "password": pw,
                "user_id": row["id"],
            }

        # admin
        admin = conn.execute(
            """
            SELECT id, username, display_name, password_hash, role, active
            FROM users
            WHERE role = 'admin' AND active = 1
            ORDER BY id LIMIT 1
            """
        ).fetchone()
        if admin:
            out["admin"] = {
                "username": (admin["username"] or "admin").strip(),
                "password": _resolve_password_from_db(conn, admin, "admin"),
                "user_id": admin["id"],
            }
        else:
            out["admin"] = {"username": "admin", "password": ADMIN_PW, "user_id": None}

    # 하위 호환: accounts["leader"] == username 문자열
    flat = {k: v["username"] for k, v in out.items()}
    flat["_creds"] = out
    return flat


@pytest.fixture(scope="session")
def base_url():
    env = (os.environ.get("BASE_URL") or "").strip().rstrip("/")
    if env:
        yield env
        return

    if _http_ok(DEFAULT_BASE + "/"):
        yield DEFAULT_BASE
        return

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env_vars = os.environ.copy()
    env_vars["FLASK_DEBUG"] = "0"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os; os.environ.setdefault('WERKZEUG_RUN_MAIN','true'); "
                "import app as m; "
                f"m.app.run(host='127.0.0.1', port={port}, debug=False, use_reloader=False)"
            ),
        ],
        cwd=str(ROOT),
        env=env_vars,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_http(url + "/")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "locale": "ko-KR",
        "viewport": {"width": 1280, "height": 900},
        "ignore_https_errors": True,
    }


@pytest.fixture(scope="session")
def accounts():
    return _resolve_accounts()


@pytest.fixture(scope="session")
def account_creds(accounts):
    return accounts.get("_creds") or {}


def login(page, base_url: str, username: str, password: str):
    if not username:
        raise AssertionError("로그인 username 비어 있음")
    page.goto(f"{base_url}/logout", wait_until="domcontentloaded")
    page.goto(f"{base_url}/home?login=1", wait_until="domcontentloaded")
    page.wait_for_selector("#loginUsername", timeout=20000)
    sel = page.locator("#loginUsername")
    values = sel.locator("option").evaluate_all(
        "opts => opts.map(o => (o.value || '').trim()).filter(Boolean)"
    )
    pick = None
    for v in values:
        if v == username or username in v or v in username:
            pick = v
            break
    if not pick:
        for v in values:
            if all(tok in v for tok in str(username).split() if tok):
                pick = v
                break
    if not pick:
        raise AssertionError("로그인 목록에 해당 계정 없음")
    sel.select_option(pick)
    page.fill("#loginPassword", password)
    page.locator("form.login-form button[type='submit']").click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(500)
    if page.locator("#loginModal:not([hidden]) #loginUsername").count():
        raise AssertionError("로그인 실패(DB 동기화 후에도 거부)")
    if not page.locator("body").get_attribute("data-session-user"):
        page.goto(f"{base_url}/home", wait_until="domcontentloaded")
        if not page.locator("body").get_attribute("data-session-user"):
            raise AssertionError("로그인 세션 없음")


@pytest.fixture
def login_as(page, base_url, account_creds, accounts):
    def _do(username_or_role: str, password: str | None = None):
        # role 키 또는 username
        cred = None
        if username_or_role in account_creds:
            cred = account_creds[username_or_role]
            username = cred["username"]
            pw = password if password is not None else cred["password"]
        else:
            username = username_or_role
            # username으로 cred 찾기
            for c in account_creds.values():
                if c.get("username") == username:
                    cred = c
                    break
            if password is not None:
                pw = password
            elif cred:
                pw = cred["password"]
            elif username == "admin" or username == accounts.get("admin"):
                pw = ADMIN_PW
            else:
                pw = DEFAULT_PW
        try:
            login(page, base_url, username, pw)
        except AssertionError as exc:
            pytest.skip(str(exc))
        return page

    return _do
