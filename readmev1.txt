================================================================================
웰딩 장비 AS 처리 시스템 - 구현 명세서 v1
================================================================================
작성 목적: 이 파일만 읽고 현재와 동일한 시스템을 처음부터 재구현할 수 있도록 기록.
프로젝트 경로 예: d:\Warehouse\wwwroot\pns as\
버전: v1 (2026-07-20 기준)
================================================================================


1. 시스템 개요
================================================================================

- 이름: 웰딩 장비 AS 처리 시스템 (HYUNDAI POWER & SYSTEM)
- 용도: 고객사 AS 접수 → 현장/공장 이력 입력 → 완료 보고서 조회·출력
- 프레임워크: Python Flask (단일 app.py 중심)
- DB: SQLite (users.db 하나에 계정·조직·접수·이력 모두 저장)
- 접수 데이터: 메모리(RECEIPTS 리스트) + SQLite receipts/report_details 테이블 동기화
- 포트: 5004
- 실행: start.bat 또는 python app.py
- 접속: http://0.0.0.0:5004 (내부 IP 예: http://10.164.38.45:5004)


2. 파일 구조
================================================================================

pns as/
├── app.py                  # Flask 메인 (라우트·비즈니스·DB·대시보드)
├── email_notify.py         # SMTP 이메일 알림 모듈
├── pdf_report.py           # AS 보고서 PDF 생성 (fpdf2)
├── requirements.txt        # flask>=3.0.0, fpdf2>=2.8.0
├── start.bat               # pip install + python app.py
├── mail.env                # SMTP 실제 설정 (git 제외 권장)
├── mail.env.example        # SMTP 설정 예시
├── users.db                # SQLite DB (런타임 생성)
├── receipts_data.migrated.json  # 구 JSON 마이그레이션 아카이브
├── readmev1.txt            # 본 문서
├── static/
│   ├── css/style.css       # 전체 UI 스타일
│   └── uploads/            # 접수·현장 사진 저장 ({receipt_no}/...)
└── templates/
    ├── base.html           # 공통 헤더·네비·로그인 모달
    ├── index.html          # 홈 (비로그인 히어로 / 로그인 대시보드)
    ├── sales.html          # 영업접수
    ├── history.html        # AS 이력관리
    ├── report.html         # AS 보고서 조회
    ├── report_body.html    # 보고서 본문 partial
    ├── status_report.html  # 경영진 현황보고 대시보드
    └── admin_users.html    # 계정·조직 관리 (admin 전용)


3. 의존성 및 환경
================================================================================

requirements.txt:
  flask>=3.0.0
  fpdf2>=2.8.0

환경변수 (선택):
  PNS_SECRET_KEY          Flask session secret (기본값 내장, 운영 시 변경)
  mail.env → os.environ   SMTP 설정 (load_mail_config로 로드)

start.bat:
  chcp 65001
  cd 프로젝트 폴더
  python -m pip install -r requirements.txt -q
  python app.py

app.py 마지막:
  app.run(host="0.0.0.0", port=5004, debug=True)


4. SQLite DB 스키마 (users.db)
================================================================================

4.1 users - 로그인 계정
  id, username(UNIQUE), password_hash, display_name, role('admin'|'staff'),
  active, created_at, updated_at
  - 최초 admin/admin 자동 생성 (role=admin)
  - 소속 인원(org_people)과 연동: ID=이름, 기본 PW=1234

4.2 teams - 부서/팀
  id, code(UNIQUE), name, category, sort_order, note
  시드 데이터:
    domestic      내수영업팀
    affiliate     관계사영업팀
    export        수출영업팀 (note: 접수 전담·AS는 내수영업팀이 처리)
    field_support 현장 지원 부서 (note: 지원요청 건만 조회·공용계정)
    executive     경영진·임원 (note: 전체 현황 조회 전용)
    service       AS팀

4.3 org_people - 소속 인원 (마스터)
  id, code(UNIQUE), name, title, role_note, team_code,
  can_receipt(0/1), can_as(0/1), sort_order, email
  - email 컬럼: ALTER TABLE로 마이그레이션 추가
  - can_receipt/can_as로 접수·AS 권한 구분
  - 인원 등록/수정 시 users 테이블 로그인 계정 자동 동기화

4.4 receipt_staff / as_staff - 레거시 담당자 마스터 (org_people로 통합 운영)

4.5 receipts - AS 접수 건 (영구 저장)
  id, receipt_no(UNIQUE), receipt_date, team_id, team,
  customer_name, customer_contact, equipment_name, symptoms,
  as_staff, as_staff_name, receipt_staff, receipt_staff_name,
  status, charge_type, billing_cost, completed_date, created_at,
  photos_json, phone, ship_date, support_quality, support_tech,
  created_by_user_id, created_by_username

4.6 report_details - AS 처리 이력 (접수번호별 JSON)
  receipt_no(PK), data_json, updated_at


5. 접수번호 규칙
================================================================================

형식: HWDS-YYMMDD-NNN
  예: HWDS-260721-001
  - YYMMDD = 접수일(receipt_date) 기준
  - NNN = 같은 날짜 내 3자리 순번 (001부터)
함수: _generate_receipt_no(), _preview_receipt_no(), _max_seq_for_date()


6. 접수 상태 (status)
================================================================================

  received     접수 (신규 등록 기본값)
  draft        임시저장 (이력관리 '임시 저장' 버튼)
  in_progress  진행중
  completed    완료

집계 규칙:
  - '진행중' 통계 = received + draft + in_progress
  - '완료' = completed
  - draft는 status_class에서 progress로 표시


7. 인증·권한
================================================================================

7.1 로그인 (/login POST)
  - 모달: base.html, ID/PW 입력 (안내 문구 없음)
  - _find_user_for_login(): users 조회 → 없으면 org_people 이름으로 자동 계정 생성
  - 비밀번호 NFC 정규화, ＠→@ 치환
  - 세션: user_id, username, user_name, user_role, csrf_token
  - admin 로그인 시 → /admin/users 리다이렉트
  - next 파라미터 지원

7.2 데코레이터
  _login_required        로그인 필수
  _admin_required        role=admin
  _executive_required    team_code=executive 또는 admin
  _service_write_locked  RECEIPTS/DB 쓰기 시 RLock

7.3 소속 인원 로그인 (_ensure_person_login)
  - username = org_people.name (한글 이름)
  - 기본 password = 1234
  - 이름 변경 시: 새 기본 PW 또는 지정 PW로 갱신
  - admin 계정은 건드리지 않음
  - _sync_org_people_logins(): 기동 시 누락 계정 자동 생성
  - _reset_org_people_passwords("1234"): admin 제외 일괄 PW 변경

7.4 접수 수정·삭제 권한 (_can_manage_receipt)
  - admin: 전체
  - created_by_user_id == 현재 user_id (등록자)
  - receipt_staff == 현재 person.id (현재 접수담당자)
  - receipt_staff_name이 현재 person.name으로 시작

7.5 계정 유형 라벨 (_account_type_label)
  executive 팀 → "전체 현황 조회 전용"
  can_receipt+can_as → "접수·AS 겸용 계정"
  can_receipt only → "접수 전담 계정"
  can_as only → "AS 담당 계정"
  그 외 → "조회 계정"


8. 팀별 데이터 범위
================================================================================

_team_scope_codes(team_code):
  executive     → None (전체 접수 표시)
  domestic      → {domestic, export}  ※ 수출 접수는 내수가 AS 처리
  기타 팀       → {해당 team_code}
  빈 값         → set() (필터 없음)

_receipt_dashboard_teams():
  executive, field_support 제외한 teams 목록
  → 대시보드·현황보고 팀별 집계에 사용


9. 라우트 목록
================================================================================

GET  /                          홈 (로그인 시 대시보드)
GET  /status-report             경영진 현황보고 (?year=2026)
POST /receipt/<no>/send-email   팀 AS 목록 수동 이메일 발송

GET  /sales                     영업접수 목록·등록·수정
GET  /api/receipt-no-preview    ?date= 접수번호 미리보기 JSON
POST /sales/register            신규 접수
POST /sales/<no>/update         접수 수정
POST /sales/<no>/delete         접수 삭제

GET  /history                   이력관리 (?tab= &receipt_no=)
GET  /api/history/list          대기 건 JSON
GET  /api/history/<no>          이력 폼 payload JSON
POST /history/save              이력 저장/임시저장/완료

GET  /report                    AS 보고서 (?receipt_no=)
GET  /api/report/<no>           보고서 JSON
GET  /report/<no>/pdf           PDF 다운로드

POST /login                     로그인
GET  /logout                    로그아웃

GET  /admin/users               계정·조직 관리
POST /admin/org/department/create|update|delete
POST /admin/org/person/create|update|delete
POST /admin/users/create|update|delete


10. 홈 대시보드 (로그인 시 index.html)
================================================================================

함수: _build_home_dashboard()

10.1 공통
  - 인사말: "{user_name}님, 안녕하세요"
  - 부제: "{team_name} · {account_label}으로 로그인되어 있습니다."
  - 바로가기 버튼 영역 (home-dash-actions)

10.2 일반 담당자 (is_executive=False)
  바로가기:
    - 신규 AS 접수 → /sales
    - 이력 입력·처리 → /history
    - AS 보고서 조회 → /report

  AS 현황 KPI (4칸):
    금일 접수 / 진행중 / 누적 완료 / 완료율

  팀 AS 목록 테이블:
    접수번호(→history), 접수일, 고객명, 장비명, 증상, 담당자, 상태, 메일
    - customer_display: 내수팀 viewer + export 접수 → "[수출영업팀접수] {고객명}"
    - 메일 칸: 이메일 발송 버튼 + staff_emails(접수·AS 담당 이메일) 나란히 표시
    - can_send_email: staff_emails 존재 여부
    - mail_enabled: mail_configured()

  내가 접수한 AS 목록:
    _is_my_receipt() = 현재 receipt_staff가 본인 (등록자 기준 아님)
    접수번호 → /sales?edit={no}#salesForm

  내 AS 처리 이력:
    REPORT_DETAILS에 저장된 건 + _is_my_as_work()
    processed_by_user_id, as_staff, as_technician 등으로 판별
    접수번호 → /report?receipt_no=

10.3 경영진·임원 (is_executive=True, team_code=executive)
  team_name = "전체"
  바로가기:
    - 현황보고 → /status-report (primary 스타일)
    - AS 보고서 조회만 표시 (신규접수·이력입력 숨김)

  AS 현황 KPI (5칸): + 전체 접수

  팀별 AS 현황 카드 (team_breakdown)

  전체 AS 목록: 담당팀 열 추가

  "내가 접수한 AS" / "내 AS 처리 이력" 섹션 숨김


11. 경영진 현황보고 (/status-report)
================================================================================

접근: _executive_required (executive 팀 또는 admin)

함수: _build_status_report(year)

11.1 연도 선택
  year_options: receipts 날짜에서 추출 + 올해
  GET ?year=2026

11.2 전체 KPI (8칸)
  전체 접수 / 미처리 / 누적 완료 / 완료율
  {year}년 접수 / {year}년 처리 / 금월 접수 / 금월 처리

11.3 월별 전체 추이
  - 1~12월 막대 차트 (접수=파랑, 처리=초록)
  - 표: 월 / 접수 / 처리(완료) / 접수-처리

11.4 팀별 요약표
  팀 / 전체 / 미처리 / 접수·임시저장·진행중 / 완료 / 완료율
  / 연간·금월 접수·처리

11.5 팀별 상세 섹션 (각 팀마다)
  - 월별 접수·처리 compact 차트
  - 미처리 목록 테이블 (접수번호→history)

집계 기준:
  월별 접수 = receipt_date의 YYYY-MM
  월별 처리 = completed_date의 YYYY-MM
  미처리 = status in (received, draft, in_progress)


12. 영업접수 (sales.html)
================================================================================

- 접수 등록 폼: 접수일, 담당팀, 고객정보, 장비, 증상, 접수/AS 담당, 유무상,
                사진 업로드, 품질/기술 지원 요청 체크
- 접수번호: 날짜 변경 시 API로 미리보기
- 목록: 상태 필터, 수정(권한 있는 건만), 삭제
- 수정: ?edit=HWDS-xxx#salesForm
- 등록 시: notify_receipt_registered() 이메일
- 담당자 변경 시: notify_staff_assigned()


13. 이력관리 (history.html)
================================================================================

- 팀 탭: teams 전체 (대기 건수 badge)
- 접수 건 선택 → /api/history/<no> 로 폼 채움
- AS 상태 선택: 접수/임시저장/진행중/완료
- 버튼 분리:
    「임시 저장」→ action=draft, status=draft
    「저장」→ action=save, 선택 status
    완료(status=completed) 시 → AS 보고서 페이지로 이동
- 저장 시 processed_by_user_id 기록 → 홈 "내 AS 처리 이력" 반영
- 완료 시 notify_receipt_completed()
- 부품 JSON, 현장 사진(field/), 지원요청 플래그 저장


14. AS 보고서 (report.html + _build_report)
================================================================================

- 접수 + report_details 합쳐 표시
- 담당자: receipt_staff/as_staff 코드 기준 _staff_name() 우선
- PDF: /report/<no>/pdf, pdf_report.py, malgun.ttf 등 한글 폰트 필요


15. 이메일 알림 (email_notify.py + mail.env)
================================================================================

15.1 설정 (mail.env)
  MAIL_SERVER=smtp.worksmobile.com
  MAIL_PORT=587
  MAIL_USE_TLS=true
  MAIL_USERNAME=...
  MAIL_PASSWORD=...        ← 네이버웍스 「외부 앱 비밀번호」 (로그인 PW 아님)
  MAIL_FROM=...
  MAIL_BASE_URL=http://10.164.38.45:5004
  (선택) MAIL_TIMEOUT=30, MAIL_RETRIES=3

15.2 load_mail_config(base_dir, force=False)
  - mail.env UTF-8-BOM 지원
  - 발송마다 force=True 재로드

15.3 send_email(to, subject, body_html) → (ok, error_code)
  - 587 실패 시 465 SSL 자동 재시도
  - DNS 실패 재시도 (기본 3회)
  - error_code: mail_not_configured, no_recipients, auth_failed,
                 dns_failed, connection_failed, send_failed

15.4 수신자 (_staff_emails)
  as_staff + receipt_staff 이메일 (org_people.email, 중복 제거)

15.5 자동 발송
  notify_receipt_registered   신규 접수
  notify_staff_assigned       담당자 변경
  notify_receipt_completed    AS 완료 → 접수 담당자
  send_receipt_notice         홈 팀 AS 목록 수동 발송

15.6 UI
  - 계정관리 admin_users.html: 인원별 email 입력 필드
  - 홈 팀 AS 목록 메일 칸: 버튼 + 이메일 주소 표시


16. 상단 네비게이션 (base.html)
================================================================================

비로그인: 홈, 영업접수, 이력관리, AS보고서 + 로그인 버튼
로그인 일반: 동일 + 사용자명 + 로그아웃
로그인 executive (current_user_is_executive):
  홈 | 현황보고 | AS보고서  (영업접수·이력관리 숨김)
admin: + 계정관리

context_processor (inject_globals):
  current_user, current_user_id, current_username, current_user_role,
  current_user_is_executive, csrf_token


17. 계정·조직 관리 (admin_users.html)
================================================================================

- 탭: 사용자 계정 / 조직·인원 마스터
- 부서(teams) CRUD: name, note
- 인원(org_people) CRUD:
    code, name, title, role_note, team, can_receipt, can_as, email
  생성/수정 시 _ensure_person_login() 연동
- admin 계정 CRUD (role, active, password)


18. 데이터 저장 흐름
================================================================================

기동:
  _init_user_db() → _load_service_data() → RECEIPTS/REPORT_DETAILS 메모리 로드
  _sync_org_people_logins()

변경:
  RECEIPTS / REPORT_DETAILS 수정 → _save_service_data()
  → SQLite receipts + report_details UPSERT
  → SERVICE_DATA_LOCK으로 동시 쓰기 방지

사진:
  static/uploads/{receipt_no}/...
  static/uploads/{receipt_no}/field/...
  photos_json / report_details.photos에 {url, tag} 형태 저장


19. report_details JSON 주요 필드
================================================================================

  report_date, phone, serial_no, re_repair(Y/N),
  receipt_staff_name, as_technician,
  processed_by_user_id, processed_by_username,
  ship_date, warranty, inspection_date, affiliation,
  process_location(현장/공장), ship_out_date,
  inspection_result, repair_content, detailed_work,
  parts[{name, qty, unit_price, subtotal}],
  support_history, support_quality, support_tech,
  photos[], remarks, as_status


20. CSS 주요 클래스 (style.css)
================================================================================

  .home-dash-*           홈 대시보드
  .home-dash-action-primary  경영진 현황보고 버튼
  .home-dash-team-grid    경영진 팀별 카드
  .receipt-mail-cell      메일 버튼+주소
  .receipt-mail-address
  .status-report-*        현황보고 페이지
  .status-kpi-card        KPI 카드
  .status-month-chart     월별 막대 차트
  .status-team-block      팀별 섹션
  .status-badge.status-received|progress|completed
  .history-tabs, .form-card, .receipt-table
  색상: --maroon (#7A1F3D 계열), 진행중 주황, 완료 녹색


21. 시드 인원 (org_people 초기값 요약)
================================================================================

  domestic:   최영호(팀장), 박용수, 장호빈(AS전담)
  affiliate:  김정훈(팀장), 박경률, 장유식, 이정건
  export:     김윤식(팀장), 이명지(접수만)
  field_support: 품질팀 계정, 기술부 계정
  executive:  영업부 부서장, 기술부 그룹장, 기술연구소장
  (로그인 ID = name 한글, PW = 1234)


22. 재구현 순서 (권장)
================================================================================

 1) Flask 앱 골격 + base.html + style.css 기본
 2) users.db _init_user_db() 전 테이블·시드
 3) 로그인·세션·org_people ↔ users 동기화
 4) RECEIPTS/report_details 로드·저장 (_load/_save_service_data)
 5) 영업접수 CRUD + 접수번호 생성
 6) 이력관리 + history_save + report_details
 7) AS 보고서 + pdf_report.py
 8) 홈 대시보드 _build_home_dashboard()
 9) email_notify.py + mail.env + org_people.email
10) 경영진 분기 (is_executive, _team_scope_codes, nav 변경)
11) status_report.html + _build_status_report()
12) admin_users 조직·계정 UI
13) start.bat, mail.env.example


23. 핵심 함수 빠른 참조 (app.py)
================================================================================

  _get_person_for_session()      세션↔org_people 매칭
  _is_executive_person()         team_code=='executive'
  _enrich_receipt()              queue, status_label, as_display 등
  _dashboard_stats_for_rows()    today/in_progress/completed/rate/total
  _build_home_dashboard()        홈 대시보드 전체
  _build_status_report(year)     현황보고
  _receipt_staff_emails()        접수+AS 담당 이메일
  _get_staff_email(code)         org_people.email
  _can_manage_receipt()          수정·삭제 권한
  _is_my_receipt()               현재 접수담당자 기준
  _is_my_as_work()               AS 처리 이력
  _dashboard_customer_label()    수출 접수 표기
  _history_payload()             이력 API 응답
  _build_report()                보고서 dict


24. 알려진 운영 참고
================================================================================

- mail.env 수정 후 Flask 재시작 필요 (발송 시 force reload 있으나 최초 load는 기동 시)
- 네이버웍스 SMTP: 외부 앱 비밀번호 + IMAP/SMTP 권한 필요
- DNS 일시 오류 시 connection_failed → dns_failed 구분, 재시도 로직 있음
- admin 로그인: admin/admin → 계정관리 화면
- debug=True 개발 서버 (운영 시 WSGI 서버 권장)

================================================================================
END OF readmev1.txt
================================================================================
