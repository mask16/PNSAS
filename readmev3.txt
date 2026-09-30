================================================================================
HYUNDAI WELDING AS 처리 시스템 — 구현 명세서 v3
================================================================================
작성일: 2026-08-18
목적: 이 파일(readmev3.txt)만 읽고 현재 운영 시스템과 동일하게 재구현할 수 있게 한다.
전제: 사내 Flask + SQLite 실서비스. 프로토타입 HTML은 참고용이며 코드를 재사용하지 않는다.
선행문서: readmev2.txt (2026-08-05). 본 문서는 v2 + 그 이후 실제 반영분(요구 7~17 등)을
         합친 현재 시스템 최종본이다. v2와 다른 규칙은 본 문서를 따른다.
프로젝트 경로 예: d:\Warehouse\wwwroot\pns as\

이 문서를 따라 구현하면 아래가 동일해야 한다.
- DB 테이블/컬럼/시드 조직
- 제품군(웰딩 장비 / 용접봉) 분리
- 권한·화면 노출·업무 규칙
- 알림(인앱+이메일) 트리거
- 홈/영업접수/이력관리/AS보고서/현황보고/개입·보상 UI 동작
- 화면 용어 「AS 대응팀」(저장 필드명은 team_id/team 유지)
- 이력관리 동시 저장 충돌 방지
- 다중 메일·안내메일 1회 발송·접수담당/AS지정/사무소 규칙


================================================================================
0. 한 줄 요약
================================================================================

엑셀 기반 “용접기·용접장비·용접봉 AS”를 웹으로 전환한 시스템.
흐름: 제품군 선택 → 로그인 → 영업접수 → 이력관리(점검·부품·비용) → AS보고서.
별도 레이어: 최고기술경영실 모니터링·에스컬레이션·보상협의·자동감지.
회사 표기: HYUNDAI WELDING (웰딩 장비·용접봉 공통).

화면에서 접수가 배정되는 영업 팀을 「AS 대응팀」이라 부른다.
(내부 필드: receipts.team_id / receipts.team. 예: 내수영업팀, 관계사영업팀, 수출영업팀, AS담당관)


================================================================================
0.1 v2 대비 반드시 반영할 변경 (재구현 시 누락 금지)
================================================================================

1) 화면 용어 「담당팀」→「AS 대응팀」
   - 적용: 영업접수 폼/목록, 홈(최근접수·팀AS·내접수·내처리이력), 필터/검색 placeholder,
     최고기술경영실 필터·열, 현황보고 팀 요약, 알림메일 표 라벨, 홈 CSV 헤더,
     대기열 문구, 모바일 CSS ::before 라벨.
   - 미변경: DB 컬럼명, Python 필드명(team_id, team), 이력 CSV 「팀명」, 소속(affiliation).

2) 로그인 성공 메시지 중복 제거
   - 로그인 폼에 1회용 login_nonce. 불일치 시 만료 처리(이미 로그인돼 있으면 홈/관리자로).
   - 홈으로 가는 성공 로그인은 flash 생략. 관리자·다른 페이지로 갈 때만
     「{이름}님, 로그인되었습니다.」 (_flash_once).
   - 홈 화면에 「○○으로 로그인되어 있습니다」 문구 없음.

3) 접수 담당자 지정
   - 일반 담당자: 로그인 본인 고정(드롭다운 없음).
   - 영업부 부서장·admin: AS 대응팀과 무관하게 내수·관계사·수출(용접봉은 AS담당관) 중 선택.
   - 사무소 공용계정: 응대한 직원 이름 직접 입력. 접수사무소는 로그인 사무소명 자동.

4) 홈 「팀 AS 목록」 메일
   - 이메일 주소 비노출. 버튼 문구는 「발송」만.
   - receipts.notice_sent=1 이면 「발송됨」, 재발송 불가.
   - 접수 등록 시 자동 메일 성공하면 notice_sent=1.
   - 홈 수동 발송 POST /receipt/<no>/send-email 성공 시에도 1.
   - CSV·홈 API에 이메일 주소 넣지 않음. can_send_email / notice_sent 만.

5) AS 담당자 지정·재지정 권한
   - 가능: 해당 AS 대응팀 팀장(최영호/김정훈/김윤식), 용접봉 AS담당관, 영업부 부서장, admin.
   - 팀장은 자기 팀 건만. 목록 버튼 문구 「지정·변경」. API /api/sales/assign-as 동일 권한.
   - 유무상·청구·부품(can_ops)은 기존대로 팀 소속이면 가능(재지정과 분리).

6) 수출 정규직 AS 대응팀 선택
   - 김윤식(kim_ys_ex)·이명지(lee_mj)만. admin·부서장은 이 예외 경로를 쓰지 않음.
   - 드롭다운: 수출영업팀 / 내수영업팀.
   - 수출 선택 → AS 담당 김윤식 고정.
   - 내수 선택 → AS 미지정(내수 팀 대기열 공유, 팀장이 지정).
   - 내수·관계사 일반 담당자: 소속팀 고정, 드롭다운 숨김(읽기전용).

7) 정직원 담당 사무소
   - 내수·관계사·수출 정직원 + 부서장 + admin: 「담당 사무소 (선택)」
     13개 사무소명 + 「해당 없음」(빈 문자열).
   - 사무소 공용계정: 이 드롭다운 숨김. office = 로그인 사무소명 자동.

8) 다중 수신 메일
   - org_people.email 한 칸에 세미콜론(또는 쉼표·줄바꿈) 구분 여러 주소 저장.
   - 관리자 UI 「+ 메일 추가」. 저장 시 emails[] → ";".join.
   - 발송 시 해당 인원의 모든 주소로 일괄 발송.

9) 이력관리 동시 저장 충돌 방지 (낙관적 잠금)
   - REPORT_DETAILS JSON에 history_updated_at, history_updated_by, history_updated_by_id.
   - 불러오기 시 시각·수정자 화면 표시 + hidden history_updated_at.
   - 임시저장/완료저장 시 제출 토큰 ≠ DB 토큰 이면 저장 거부, flash 후 재조회 안내.
   - 이력 화면의 즉시 공유/보고도 토큰 검사 + 성공 시 토큰 갱신(본인 공유 후 저장이 막히지 않게).
   - 영업접수 쪽 공유 API는 history_updated_at 키가 없으면 검사 생략. 실제 공유가 일어나면 시각은 갱신.

10) 헤더 메뉴
    - .nav-link { white-space: nowrap; flex-shrink: 0; } 관리자 메뉴가 줄바꿈되지 않게.


================================================================================
1. 런타임·기술 스택
================================================================================

- 언어: Python 3
- 프레임워크: Flask >= 3.0.0
- PDF: fpdf2 >= 2.8.0
- DB: SQLite 파일 1개  users.db
- 세션: Flask session (secret: 환경변수 PNS_SECRET_KEY, 없으면 내장 기본값)
- 포트: 5004, host 0.0.0.0, debug=True (개발)
- 실행: start.bat 또는 python app.py
- 문자셋: start.bat에서 chcp 65001
- 의존성 파일: requirements.txt
    flask>=3.0.0
    fpdf2>=2.8.0

환경변수:
  PNS_SECRET_KEY          Flask secret
  MAIL_*                  mail.env에서 로드 (아래 20장)
  MAIL_BASE_URL           메일 본문 링크용 베이스 URL

기동 시:
  1) users.db 스키마 생성/마이그레이션 (없는 컬럼 ALTER 포함, notice_sent 등)
  2) 기본 팀·인원 시드 (없을 때만 / 필수 시스템 팀만 보강)
  3) org_people ↔ users 로그인 계정 동기화 (이름=ID, 기본 PW=1234, admin 제외)
  4) receipts + report_details 를 메모리 RECEIPTS / REPORT_DETAILS 로 로드
  5) v32_app_ext.install() 로 권한·알림·청구·자동감지 API 주입
  6) /rods* 라우트 등록

start.bat 안내 주소 예:
  외부: http://10.164.38.45:5004/
  내부: http://43.201.229.107:5004/


================================================================================
2. 디렉터리·파일 구조 (재구현 시 동일하게)
================================================================================

pns as/
├── app.py                      # 메인 Flask 앱 (라우트·DB·대시보드·접수·이력·보고서)
├── v32_rules.py                # 순수 업무규칙 (세션 비의존): 워런티·재수리·마진·자동감지
├── v32_app_ext.py              # app.py 몽키패치: 권한/알림/청구/부품/과거이력 API
├── product_line_ext.py         # 제품군(welding/rods) 세션·라벨·조직 시드
├── email_notify.py             # SMTP 메일 발송 (다중 수신자)
├── pdf_report.py               # AS보고서 PDF
├── qa_verify.py                # 회귀 QA (python qa_verify.py)
├── requirements.txt
├── start.bat
├── mail.env                    # 실제 SMTP (비밀, 배포 제외)
├── mail.env.example
├── users.db                    # 런타임 SQLite (계정+조직+접수+이력+알림)
├── readmev1.txt                # 구버전 명세 (참고)
├── readmev2.txt                # 이전 명세 (2026-08-05)
├── readmev3.txt                # 본 문서 (현재 시스템)
├── static/
│   ├── css/style.css           # CI 마룬(#7a1f3d 계열), 반응형
│   └── uploads/                # 사진 파일 {receipt_no}/...
└── templates/
    ├── base.html               # 헤더·네비·햄버거·로그인모달·알림벨·제품군변경
    ├── select_product.html     # 제품군 게이트 (웰딩 장비 / 용접봉)
    ├── index.html              # 홈 대시보드
    ├── rods_home.html          # 용접봉 비로그인 랜딩
    ├── sales.html              # 영업접수
    ├── history.html            # 이력관리
    ├── report.html             # AS보고서 (JS 재조회)
    ├── report_body.html        # 보고서 본문 partial
    ├── status_report.html      # 기간별 현황보고
    ├── tech_monitor.html       # 개입·보상
    └── admin_users.html        # 계정·조직 관리 (admin)

선택 유틸(운영용, 재구현 필수 아님):
  seed_rods_sample.py, seed_v32_demo.py, seed_assignee_test_data.py,
  migrate_v32.py, fix_role_flags.py
  templates/rods_placeholder.html  (용접봉 자리표시, 본 흐름 필수 아님)

CSS 캐시 무효화: templates/base.html 에서
  style.css?v=20260818b  (파일 내용 바꿀 때마다 쿼리 버전 올릴 것)


================================================================================
3. 데이터 저장 모델 (매우 중요)
================================================================================

3.1 이원 구조
- 계정/조직/알림: SQLite를 직접 CRUD.
- 접수(RECEIPTS) + 이력(REPORT_DETAILS): 기동 시 DB → 메모리 dict/list,
  변경 시 전체를 DB에 다시 씀 (_save_service_data).
- _save_service_data():
    DELETE FROM report_details;
    DELETE FROM receipts;
    RECEIPTS 전체 INSERT;
    REPORT_DETAILS 전체 INSERT;
      updated_at 컬럼 = detail.history_updated_at 가 있으면 그 값, 없으면 now
  반드시 RLock(SERVICE_DATA_LOCK) + @_service_write_locked 로 직렬화.

3.2 전역
  RECEIPTS: list[dict]     # 영업접수 + 상태/청구/개입 필드
  REPORT_DETAILS: dict[receipt_no -> dict]  # 이력관리 상세 JSON

3.3 사진
- 실제 파일 저장: static/uploads/{receipt_no}/
- 허용 확장자: .jpg .jpeg .png .gif .webp .bmp
- 접수 시 고객 전달 사진: receipt.photos = [{url, tag:'customer'}, ...]
- 이력 시 현장 사진: detail.photos = [{url, tag:'before'|'after'|'parts'|'customer'}, ...]
- 태그 4종: 고객 전달 / 수리 전 / 수리 후 / 교체 부품(용접봉은 제품 교환)
- 여러 장 동시 업로드, 카메라 capture 지원 (모바일)

3.4 제품군 스코프
- session['product_line'] = 'welding' | 'rods'
- receipt.product_line = 'equipment' | 'rod'  (미지정은 equipment)
- 모든 목록·집계·CSV·자동감지는 현재 세션 제품군만 (_line_receipts)
- '/' 접속 시 session.clear() 후 제품군 선택 화면
- 제품군 변경(/switch-product) 시 session.clear() → 로그인 초기화
- /home 진입 시 welding, /rods 진입 시 rods. 제품군이 바뀌면 로그인 세션 제거


================================================================================
4. SQLite 스키마 (users.db) — 최종 컬럼 전부
================================================================================

4.1 users
  id INTEGER PK AI
  username TEXT UNIQUE COLLATE NOCASE
  password_hash TEXT
  display_name TEXT
  role TEXT DEFAULT 'staff'          -- 'admin' | 'staff'
  active INTEGER DEFAULT 1
  created_at TEXT, updated_at TEXT

최초 시드:
  username=admin / password=admin / role=admin / display_name=관리자

org_people 동기화 규칙:
  - username = org_people.name (한글 이름)
  - 기본 비밀번호 = 1234
  - admin 계정은 동기화·PW리셋에서 제외
  - 로그인 시 users에 없으면 이름으로 org_people 매칭 후 계정 자동 생성 가능

4.2 teams
  id INTEGER PK AI
  code TEXT UNIQUE COLLATE NOCASE
  name TEXT
  category TEXT DEFAULT 'domestic'
  sort_order INTEGER DEFAULT 0
  note TEXT DEFAULT ''

시드 팀 (code / name / 비고):
  sales_office   지역 영업사무소     13개 사무소 공용 · 제품군 공용 (sort 5)
  domestic       내수영업팀
  affiliate      관계사영업팀
  export         수출영업팀          접수 전담 · AS는 내수(장호빈) 처리(기본 라우팅)
  field_support  현장 지원 부서      지원요청 건만 조회 · 공용계정
  sales_head     영업부 부서장       접수+전권의뢰 · 이력관리 불가
  executive      경영진 · 임원       전체 조회 전용
  tech_mgmt      최고기술경영실      모니터링·개입·보상
  rods_as        AS담당관실          용접봉 전용 · 최고기술경영실 산하
  service        AS팀                (레거시, 관리자 삭제 시 재생성하지 않음)

app_meta 키 team_removed:{code} 가 있으면 해당 시스템 팀을 기동 시 재생성하지 않음.
sales_head / service 는 관리자가 지우면 자동 복구하지 않음.
domestic/affiliate/export/field_support/executive/tech_mgmt 는 삭제 기록 없으면 보강.

4.3 org_people (권한 마스터)
  id INTEGER PK AI
  code TEXT UNIQUE COLLATE NOCASE     -- 내부 ID (jang_hb 등)
  name TEXT                           -- 로그인 username과 매칭
  title TEXT DEFAULT ''
  role_note TEXT DEFAULT ''
  team_code TEXT                      -- teams.code
  can_receipt INTEGER DEFAULT 1       -- 영업접수 가능
  can_as INTEGER DEFAULT 1            -- 이력관리 가능
  sort_order INTEGER DEFAULT 0
  email TEXT DEFAULT ''               -- SMTP 수신. 여러 주소는 ';' 구분 한 문자열
  product_line TEXT DEFAULT 'equipment'
      -- 'equipment' | 'rod' | 'both'

이메일 파싱 규칙 (_parse_person_emails):
  구분자 ; , 줄바꿈. '@' 없는 토큰 무시. 대소문자 무시 중복 제거.
  저장 정규화: ";".join(파싱결과)
  메모리 person.emails = 리스트, person.email = 원문 문자열

4.4 receipt_staff / as_staff
  레거시 마스터. 실운영은 org_people. 기동 시 일부 샘플 INSERT OR IGNORE만 수행.
  신규 기능은 org_people.can_receipt / can_as 를 사용.

4.5 receipts (영업접수 + 상태/비용/개입)
  id INTEGER PK                         -- 앱에서 uuid int 또는 기존 id
  receipt_no TEXT UNIQUE                -- HWDS-YYMMDD-NNN 또는 RD-YYMMDD-NNN
  receipt_date TEXT                     -- YYYY-MM-DD
  team_id TEXT                          -- domestic/affiliate/export/rods_as 등 (AS 대응팀 코드)
  team TEXT                             -- 표시명 (내수영업팀 등)
  customer_name TEXT
  customer_contact TEXT
  equipment_name TEXT                   -- 용접봉이면 제품명
  symptoms TEXT
  as_staff TEXT                         -- org_people.code, 미지정이면 ''
  as_staff_name TEXT
  receipt_staff TEXT                    -- org_people.code (사무소 직접입력 시 '')
  receipt_staff_name TEXT               -- 화면에 보이는 접수담당자 이름
  status TEXT DEFAULT 'received'
      received | draft | in_progress | parts_waiting | on_hold | completed
  charge_type TEXT DEFAULT 'free'       -- paid | free
  billing_cost TEXT                     -- 정수 문자열 또는 NULL
  completed_date TEXT
  created_at TEXT ISO
  photos_json TEXT DEFAULT '[]'         -- 고객전달 사진 배열
  phone TEXT
  ship_date TEXT                        -- 최초출하일
  support_quality INTEGER DEFAULT 0
  support_tech INTEGER DEFAULT 0
  created_by_user_id INTEGER
  created_by_username TEXT
  -- 최고기술경영실
  severity TEXT DEFAULT 'medium'        -- low|medium|high|critical (레거시)
  intervention_status TEXT DEFAULT 'none'
      none | watching | intervening | done
  compensation_status TEXT DEFAULT 'none'
      none | reviewing | negotiating | agreed | dispute
  intervention_note TEXT DEFAULT ''
  compensation_note TEXT DEFAULT ''
  intervention_by TEXT DEFAULT ''
  intervention_at TEXT
  escalation_level TEXT DEFAULT 'normal'
      normal | monitor | critical | cleared | closed
  compensation_amount TEXT DEFAULT ''
  intervention_log TEXT DEFAULT '[]'    -- [{date, by, note}, ...]
  -- 청구
  billing_margin_rate TEXT              -- NULL이면 기본 30, '0'은 0%
  billing_manual INTEGER DEFAULT 0      -- 1이면 직접입력(협의가) 유지
  billing_set_by TEXT DEFAULT ''        -- 영업이 책정한 사람 이름
  -- 제품군·사무소
  product_line TEXT DEFAULT 'equipment' -- equipment | rod
  spec TEXT DEFAULT ''                  -- 용접봉 규격 (필수)
  lot TEXT DEFAULT ''                   -- 용접봉 로트 (선택)
  office TEXT DEFAULT ''                -- 사무소명. 공용계정 자동 또는 정직원 선택값
  notice_sent INTEGER NOT NULL DEFAULT 0
      -- 1이면 안내 메일 이미 발송. 홈 「발송됨」, 재발송 거부

4.6 report_details
  receipt_no TEXT PK
  data_json TEXT NOT NULL               -- 아래 4.8 JSON
  updated_at TEXT                       -- 가능하면 history_updated_at 과 동일

4.7 notifications (인앱 알림함)
  id INTEGER PK AI
  user_code TEXT DEFAULT ''             -- 개인 지정 시 org_people.code
  team_code TEXT DEFAULT ''             -- 팀 전체 발송 시 teams.code 또는 office_* 또는 person.id
  title TEXT
  body TEXT DEFAULT ''
  link TEXT DEFAULT ''                  -- /history | /report | /tech-monitor
  receipt_no TEXT DEFAULT ''
  is_read INTEGER DEFAULT 0
  created_at TEXT

조회 매칭:
  (user_code != '' AND user_code = 로그인 person.code)
  OR (user_code = '' AND team_code IN (person.team_code, person.id))
  ※ 사무소 완료알림은 team_code=office_ulsan 처럼 사무소 person.id 를 씀.
     사무소 계정 로그인 시 person.id 와 team_code 둘 다 IN 절에 넣어 수신.

4.8 report_details.data_json 필드
  report_date, phone, serial_no,
  re_repair ('Y'|'N' 또는 레거시 문자열),
  receipt_staff_name, as_technician,
  processed_by_user_id, processed_by_username,
  ship_date, warranty, inspection_date,
  affiliation,                 -- 소속 표시값 (내수영업팀 / AS담당관 / 기술연구소 등)
  process_location,            -- '현장'|'공장' 또는 field/factory
  ship_out_date,
  inspection_result, repair_content, detailed_work,
  parts: [{name, spec, qty, unit_price, subtotal}, ...],
  support_history, support_quality, support_tech, support_tech_mgmt,
  photos: [{url, tag}, ...],
  remarks, as_status,
  -- 동시저장 잠금 (이력관리)
  history_updated_at,          -- 'YYYY-MM-DD HH:MM:SS' 문자열. 없으면 ''
  history_updated_by,          -- 수정자 표시명
  history_updated_by_id        -- org_people.code 또는 session user_id

4.9 app_meta
  key TEXT PK, value TEXT
  용도: team_removed:{code} 등


================================================================================
5. 조직·계정 시드 (재구현 시 동일 code 사용)
================================================================================

5.1 웰딩 장비 (product_line=equipment, 최고기술경영실·품질·사무소는 both)

code            name            team_code      can_receipt can_as  권한유형
choi_yh         최영호(팀장)     domestic       1           1      IA  ※내수 팀장, AS 재지정
park_ys         박용수           domestic       1           1      IA
jang_hb         장호빈           domestic       0           1      A   ※수출 AS 전담(기본 라우팅)
kim_jh_aff      김정훈(팀장)     affiliate      1           1      IA  ※관계사 팀장, AS 재지정
park_kr         박경률           affiliate      1           1      IA
jang_ys         장유식           affiliate      1           1      IA
lee_jg          이정건           affiliate      1           1      IA
kim_ys_ex       김윤식(팀장)     export         1           1      IA  ※수출 팀장, AS대응팀 선택, 본인+수출건
lee_mj          이명지           export         1           0      I   ※이력 메뉴 숨김, AS대응팀 선택 가능
quality_acc     품질팀 계정      field_support  0           0      D   both
tech_acc        기술부 계정      field_support  0           0      D   웰딩만
sales_head      영업부 부서장    sales_head     1           0      X   웰딩만
tech_group      기술부 그룹장    executive      0           0      E
tech_lab        기술연구소장     executive      0           0      E
exec_chief      총괄             executive      0           0      E
exec_ceo        대표이사         executive      0           0      E
tech_mgmt_office 최고기술경영실  tech_mgmt      0           0      C   both

상수 (app.py):
  EXPORT_AS_HANDLER_CODE = jang_hb
  EXPORT_AS_HANDLER_NAME = 장호빈
  EXPORT_AS_SELF_CODE    = kim_ys_ex
  EXPORT_AS_ALLOWED      = {jang_hb, kim_ys_ex}
  EXPORT_RESPONSE_CHOOSER_CODES = {kim_ys_ex, lee_mj}
  SALES_TEAM_LEADER_CODES = {choi_yh, kim_jh_aff, kim_ys_ex}
  SALES_RECEIPT_STAFF_TEAMS = (domestic, affiliate, export)

5.2 지역 영업사무소 (product_line=both, team=sales_office, can_receipt=1, can_as=0)
  product_line_ext.SALES_OFFICES 순서 고정:
  office_seoul 서울사무소
  office_incheon 인천사무소
  office_suwon 수원사무소
  office_daejeon 대전사무소
  office_gwangju 광주사무소
  office_jeonju 전주사무소
  office_daegu 대구사무소
  office_busan 부산사무소
  office_changwon 창원사무소
  office_pohang 포항사무소
  office_ulsan 울산사무소
  office_ulsan_hi 울산중공업사무소
  office_samho 삼호사무소

사무소 로그인 = 공용계정. 접수 시:
  - receipt_staff_name = 그때 응대한 개인 이름 (직접 입력, 필수)
  - office = 로그인한 사무소명 (자동, 드롭다운 없음)
  - receipt_staff 코드는 빈 문자열
  - 홈/알림은 office 기준

5.3 용접봉 (product_line=rod)
code          name              team_code      can_r can_as
rods_officer  AS담당관          rods_as        1     1     ※접수·의뢰 겸용, AS 재지정 가능
rods_a        A담당자           rods_as        0     1
rods_b        B담당자           rods_as        0     1
rods_c        C담당자           rods_as        0     1
rods_d        D담당자           rods_as        0     1
rods_lab      기술연구소 계정   field_support  0     0     ※용접봉 지원부서

용접봉 로그인 목록에 노출:
  13 사무소 + AS담당관실 5인 + 품질팀 + 기술연구소 + 최고기술경영실
숨김(웰딩 전용):
  내수/관계사/수출 인원, 영업부 부서장, 기술부 계정, 경영진(그룹장·연구소장·총괄·대표)

5.4 로그인 드롭다운
- 제품군에 보이는 인원만 그룹(팀)별로 표시
- admin 은 별도
- 간이 로그인이 아니라 ID/PW. 다만 실무자는 이름=ID, PW=1234
- 실서비스 SSO는 미구현 (요구사항 9장 제약)
- 로그인 POST에 hidden login_nonce 필수 (세션의 값과 일치해야 함)


================================================================================
6. 권한 매트릭스 (화면)
================================================================================

권한 약호:
  I  접수 전담 (이명지, 사무소)
  A  AS 전담 (장호빈, rods_a~d)
  IA 겸용
  X  영업부 부서장
  E  경영진
  D  지원부서 (품질/기술/기술연구소)
  C  최고기술경영실
  admin 전체

화면          I     A      IA     X              E            D              C
홈            개인  개인   개인   본인접수+전사  전사         지원요청목록   심각카드+전사+정확도
영업접수      O     숨김   O      O(전팀편집)    숨김         숨김           숨김
이력관리      숨김  O      O      숨김           숨김         O(지원건만)    숨김
AS보고서      조회  조회   조회   조회           전건조회     전건조회       조회+개입관리버튼
현황보고      -     -      -      O              O            -              O
개입·보상     -     -      -      -              -            -              O
계정관리      -     -      -      -              -            -              -  (admin만)

규칙:
- 권한 없는 화면은 안내 메시지가 아니라 상단 탭·홈 바로가기 카드 자체를 숨긴다.
- 직접 URL 접근 시 flash 후 홈으로.
- C는 영업접수·이력관리 접근 불가 (실무가 아닌 개입·모니터링).
- X는 이력관리 불가. 어느 팀 누구에게나 AS 지정 가능. 유무상·가격·부품·재지정 전 건 편집.
  접수 담당자도 3팀에서 선택 가능.
- E는 입력 없음. 조회·CSV만.
- D는 자기 부서로 지원요청된 건만 이력에서 조회(품질=support_quality, 기술/연구소=support_tech).
- 용접봉에는 X·E 계정 자체가 로그인 목록에 없음. C만 경영진급으로 양 제품군 로그인.

메뉴 노출 헬퍼 (구현명):
  _can_access_sales(person)
  _can_access_history(person)
  _is_sales_head / _is_executive_person / _is_tech_mgmt_person
  _is_office_person  (team_code==sales_office 또는 code startswith office_)
  _can_choose_receipt_staff  (admin 또는 부서장)
  _can_reassign_as_staff(receipt, person)
  _can_pick_assigned_office
  _is_export_response_chooser
  _is_sales_team_leader

영업접수 목록 조회범위:
  - 사무소 계정: receipts.office = 자기 사무소만
  - 일반 사원: receipts.team_id = 자기 팀만
  - 부서장/경영진/최고기술경영실/admin: 전체
  - AS보고서는 예외로 전사 조회 유지 (_filter_receipts_for_sales_list 미적용)


================================================================================
7. 전체 업무 흐름
================================================================================

① 제품군 선택 (웰딩 장비 / 용접봉)
② 로그인 (ID/PW + login_nonce)
③ 영업접수 등록
   - 동일 고객사 과거이력 실시간 조회
   - AS 대응팀: 권한에 따라 고정 또는 선택 (10.6, 11.1)
   - 접수 담당자: 본인 고정 또는 부서장 선택 또는 사무소 이름 입력
   - 담당 사무소: 정직원 선택 / 공용계정 자동
   - 필요 시 AS담당 사전지정(권한자만), 지원요청 체크
   - 용접봉: 규격 필수, 로트 선택, AS 대응팀=AS담당관만
④ 이력관리에서 대기 건 조회 (과거이력·워런티·재수리 자동표시)
⑤ 점검·수리/조치, 부품/제품교환, 사진, 비용 입력
⑥ AS상태: 진행중/부품대기/보류 → 임시저장만
           완료 → 「완료 처리 & 저장」만 가능
⑦ 저장 시 낙관적 잠금 검사 → 통과하면 영업접수 목록·홈 자동 반영, self-assign
⑧ AS보고서에서 실데이터 조회·인쇄·PDF
⑨ (선택) 최고기술경영실 보고 / 자동감지 검토 / 개입·보상


================================================================================
8. 접수번호
================================================================================

웰딩 장비: HWDS-YYMMDD-NNN
용접봉:     RD-YYMMDD-NNN
- YYMMDD = receipt_date 기준 (등록일이 아님)
- NNN = 해당 제품군·해당일 순번 001~
- 미리보기 API: GET /api/receipt-no-preview?date=YYYY-MM-DD
- 수정 불가
- 일 999건 초과 시 ValueError


================================================================================
9. AS 상태·저장 정책
================================================================================

상태값:
  received      접수 (신규)
  draft         임시저장 (레거시/내부, UI에서는 진행중으로 취급하는 경우 있음)
  in_progress   진행중
  parts_waiting 부품대기   ※용접봉 UI에 없음. 저장 시 in_progress로 강제
  on_hold       보류
  completed     완료

저장 버튼:
  진행중/부품대기/보류 → 「임시 저장」만
  완료 선택 시에만 「완료 처리 & 저장」 활성화
  완료가 아닌데 complete 액션이면 서버가 거부

완료 후:
  - 이력관리 대기목록에서 제외, 재편집 불가 (과거 이력 무결성)
  - 홈 「내 AS 처리 이력」에는 진행+완료 모두 표시
  - 영업접수 목록·홈 내 접수목록에는 상태 뱃지로 항상 표시
  - AS보고서는 상태 무관 조회
  - 홈에서 완료 건 클릭 → 보고서. 미완료+이력권한 → 이력관리.
    이력권한 없는 계정(I, X, E, C) → 보고서.

미완료 우선 정렬:
  영업접수 목록, 홈 개인화 목록, 경영진/부서장 전체 이력, CSV 외 화면 목록에서
  미완료가 완료보다 항상 위 (v32.incomplete_first_key).


================================================================================
10. 담당자 지정 · self-assign · 대기순번 · 팀 가시성
================================================================================

10.1 AS 담당 지정·재지정
- 목록/폼에서 「지정·변경」은 _can_reassign_as_staff 가 참일 때만.
  조건: admin 또는 영업부 부서장 또는 (팀장 AND person.team_code == receipt.team_id)
  팀장 판정: code in {choi_yh, kim_jh_aff, kim_ys_ex} 또는 rods_officer
            또는 title에 「팀장」, 또는 용접봉 팀+이름 AS담당관
- 미지정 시 AS 대응팀 대기열에서 자율 처리.
- 수출 신규 접수(일반 경로): 지정 기본값 장호빈. 후보=장호빈·김윤식·미지정만.
  (부서장·admin은 예외적으로 전원 후보 가능)
- 수출 정규직 대응팀 선택 경로: 11.1

10.2 self-assign
- 이력관리에서 저장한 사람이 as_staff / as_staff_name 으로 덮어씀.
- 지정자와 다른 사람이 저장하면 confirm 후 진행. 취소 시
  「저장이 취소되었습니다」 alert. 데이터 미저장.

10.3 대기순번 (영업접수 목록 + 홈 「내가 접수한 AS」)
- 지정된 건: 그 담당자 미완료 건 중 접수순 N / M번째 (○○ 담당 대기열)
- 미지정: 처리팀 미지정 대기열 기준 N / M번째 (팀명 또는 「AS 대응팀」 대기열)
- 완료: 「완료됨」
- 부서장 홈의 본인 접수에도 순번 표시. 경영진·최고기술경영실 전사는 순번 생략 가능.

10.4 팀 리더 가시성
- 내수 팀원 홈 「팀 AS 목록」에 수출→장호빈 라우팅 건 표시.
- 고객명 앞에 [수출영업팀접수] 태그.
- 담당자(지정 또는 실처리) 함께 표시.
- 최영호·박용수 이력관리 대기목록에는 수출 건 비노출.
- 장호빈은 내수 탭(+수출 탭)에서 수출 건 처리.
- 김윤식은 수출 탭에서 수출 건 처리 가능(이명지 접수 포함).
  스펙 6.1의 “본인 접수건만”보다 조직표(이명지 AS=장호빈 또는 김윤식)를 따름.

10.5 용접봉 배분
- 사무소 접수 → AS담당관실 전체 대기목록에 노출 (자율).
- AS담당관이 「지정·변경」으로 1차 분배 가능.
- 지정 후에도 다른 A~D 담당자 대기목록에서 사라지지 않음 (이어받기 가능).

10.6 접수 담당자
- 일반 담당자: 로그인 본인 id/이름 고정. 서버도 _resolve_receipt_staff 가 요청값을 무시.
- 부서장·admin: 내수·관계사·수출(용접봉: rods_as) can_receipt 인원 트리에서 선택.
  AS 대응팀 드롭다운 값과 무관.
- 사무소: receipt_staff='', receipt_staff_name=입력값. 이름 없으면 저장 거부.
- 안내: 「AS 대응팀과 무관하게 … 담당자를 지정합니다. 완료 알림은 지정한 담당자에게 발송됩니다.」


================================================================================
11. 수출 라우팅 · AS 대응팀 선택 (웰딩 장비)
================================================================================

11.1 수출 정규직(김윤식·이명지) 접수 폼
- _is_export_response_chooser: 용접봉 아님, admin 아님, 부서장 아님,
  code in {kim_ys_ex, lee_mj} 또는 (team_code=export 이고 이름이 김윤식/이명지로 시작).
- AS 대응팀 드롭다운: 수출영업팀(export, 신규 기본), 내수영업팀(domestic).
- 힌트: 수출이면 김윤식이 AS 담당. 내수이면 내수 건과 같이 팀에 공유되고 팀장이 지정.
- 신규: AS 담당 입력은 읽기전용 「김윤식」, hidden as_staff=kim_ys_ex.
  서버 _apply_export_response_as_staff:
    team_id=export → kim_ys_ex
    team_id=domestic → '' (미지정)
- 수정 시 팀이 바뀌면 위 규칙으로 AS 담당을 다시 맞춤.

11.2 기본 수출 라우팅 (그 외)
- 수출 접수는 내수 이력 탭에도 매칭되지만, 실제 노출·처리는 장호빈(및 admin, 김윤식).
- 최영호·박용수: 이력 대기·처리 모두 불가.
- 김윤식: 수출 탭에서 조회·처리 가능.
- 이명지: 이력 메뉴 숨김.
- 청구비용 「가격책정」: 수출팀 소속(및 부서장)에게만. 내수 AS(장호빈)에게는 목록 버튼 비노출.
  (계약가 권한이 수출에 있음. 이력관리에서는 AS가 조정 가능)

11.3 내수·관계사 일반 담당자
- AS 대응팀 드롭다운 숨김. hidden team = 소속팀. 라벨은 읽기전용 팀명.


================================================================================
12. 지원요청
================================================================================

체크:
  품질팀에 공유 → support_quality
  기술부에 공유 → support_tech          (용접봉 라벨: 기술연구소에 공유)
  최고기술경영실 보고 → support_tech_mgmt (이력 상세에 저장)
    → escalation_level 이 normal 이면 monitor 로 상향, intervention_status=watching

접수 시점 또는 이력 처리 중 언제든 추가 가능. 배열/누적 메모는 support_history 문자열.

즉시 반영: 체크 후 「공유하기/보고하기」 → POST /api/support-share
  JSON: {receipt_no, kind: quality|tech|tech_mgmt, history_updated_at?}
  이력 화면은 history_updated_at 를 반드시 보냄 (충돌 검사).
  영업접수 화면은 키를 안 보냄 (검사 생략, 성공 시 수정시각은 갱신).
  이미 공유됨(already=true)이면 시각을 바꾸지 않음.
  충돌이면 HTTP 409, {ok:false, conflict:true, error:...}

품질팀 이력: action=share_tech 폼 저장으로 기술부 이관. 동일 잠금 검사.

D 계정 로그인 시 해당 플래그 건만 이력 목록에 표시.


================================================================================
13. 유상/무상 · 청구비용 · 부품
================================================================================

13.1 유무상
- 접수 후에도 AS 대응팀 소속(또는 X)이 변경 가능. (_can_edit_sales_ops)
- 유상→무상: billing_cost, billing_manual, billing_margin_rate, billing_set_by 초기화.

13.2 마진 계산
  기본 마진율 30%.
  billing_margin_rate 가 NULL/'' 이면 30, '0'이면 0% (미설정과 0 구분).
  청구비용 = round(부품합계 * (1 + 마진율/100))
  또는 billing_manual=1 이면 직접 입력 금액 유지.

13.3 영업접수 목록에서 책정
- AS 대응팀 소속 또는 부서장: 「가격책정」
- 수출 건은 수출팀(+부서장)만 버튼 노출.
- 책정 후 billing_set_by = 영업 이름.
- 이력관리에 「영업 담당자가 이미 책정함 (이름). AS담당자가 필요 시 조정 가능」.

13.4 영업 부품관리
- 「부품관리」 팝업: 행 추가/삭제, name/spec/qty/unit_price, 소계·합계 자동.
- records[].parts = REPORT_DETAILS[no].parts 공유. 마지막 저장 우선.
- 유상 + billing_manual=0: 저장 시 마진 재계산.
- 유상 + billing_manual=1: 청구금액 유지, 부품합계만 갱신.
- 무상: 청구 영향 없음 (원가 기록).

13.5 이력관리 부품 표
- 웰딩: 부품명 · 규격 · 수량 · 단가 · 소계
- 용접봉: 제품명 · 규격 · 수량(KG) · 단가 · 소계
- 규격 컬럼은 양쪽 공통. 행 0개까지 허용.


================================================================================
14. 재수리 · 워런티 · 과거이력
================================================================================

14.1 재수리 자동제안
- 고객명+장비명 완전 일치.
- 양쪽에 serial_no 가 있으면 추가로 대조.
- 과거 completed 이력이 하나라도 있으면 Y 제안. 기간 제한 없음.
- 담당자가 수정 가능. 수정하면 자동판정 표시 제거.
- 시리얼 없는 케이블·토치 등은 고객+장비만으로 판정.

14.2 워런티
- 최초출하일(ship_date) ~ 점검일(없으면 오늘) ≤ 365일 → 「워런티 대상」
- 출하일 없음 → 「판단 불가」
- 초과 → 「워런티 만료」

14.3 과거이력
- 영업접수: 고객명 입력 즉시.
- 이력관리: 접수건 조회 즉시.
- 동일 장비 건 별도 표시.
- 접수번호 클릭 → AS보고서 + return_url 로 원래 화면 복귀 버튼.
- 매칭은 문자열 완전 일치 (알앤티 ≠ 알앤티(주)).


================================================================================
15. 알림 (인앱 + 이메일)
================================================================================

15.1 트리거

시점                              수신                         제목 예
영업접수 (AS 지정)                지정 AS 1인                  [AS접수] HWDS-… 신규 접수 - 고객 (장비)
영업접수 (미지정)                 처리팀 AS가능 전원           [AS접수] (미지정) 신규 접수 …
                                  수출 미지정 → jang_hb+kim_ys_ex
                                  미지정 인앱 body: 「AS 대응팀 대기열에 등록되었습니다.」
이력 완료 처리                    접수담당자 개인
                                  office 있는 건 → 사무소 team 알림
                                                               [AS완료] … 처리자·완료일·(유상)청구비용
이력 지원요청 신규 체크           품질팀 또는 기술부/연구소    [지원요청] …
이력 최고기술경영실 보고 신규     tech_mgmt 팀                 [지원요청] … 최고기술경영실 보고
에스컬레이션 등급 변경            AS담당(없으면 접수담당)      [최고기술경영실] 등급 변경: 일반 → 심각
영업접수에서 AS 재지정            신규 지정자                  [담당지정] / [AS 배정] 메일
홈 수동 안내메일                  접수·AS 담당자 등록 메일     send_receipt_notice

15.2 인앱
- 헤더 벨 아이콘, 본인 건만, 클릭 시 읽음.
- 처리 가능(can_history) → /history?receipt_no=
- 조회만 → /report?receipt_no=
- tech-monitor 링크는 그대로 (focus=접수번호).
- 전체 읽음 API 있음.

15.3 이메일 (email_notify.py)
- mail.env 없으면 발송 스킵 (인앱은 유지).
- get_staff_email(code) 는 세미콜론 구분 문자열 또는 빈 값.
  _parse_staff_emails 로 리스트화 후 MIME To 에 콤마 조인, SMTP 수신자 전원.
- 함수:
    notify_receipt_registered(receipt, get_email, base_url, recipient_codes=)
    notify_receipt_completed(...)
    notify_support_requested(..., target_code, target_label, requester)
    notify_tech_mgmt_reported(..., requester)  → email of tech_mgmt_office
    notify_escalation_changed(..., old_label, new_label)
    notify_staff_assigned(receipt, staff_code, role_label, ...)
    send_receipt_notice(...)  # 홈에서 수동 발송
- 메일 표 라벨: 「AS 대응팀」 (receipt.team 값)
- SMTP: STARTTLS 587 또는 SSL 465, 재시도 3회.
- 수신 이메일은 org_people.email (다중 가능).

15.4 안내메일 1회 (notice_sent)
- 신규 접수 자동 메일이 성공하면 receipts.notice_sent=1, _save_service_data.
- 홈 「발송」은 notice_sent=0 이고 can_send_email(등록된 메일 있음)일 때만.
- 발송 성공 → notice_sent=1. 이미 1이면 「이미 안내 메일이 발송된 건입니다.」
- 화면에는 주소를 넣지 않는다.

mail.env 키:
  MAIL_SERVER, MAIL_PORT, MAIL_USE_TLS, MAIL_USE_SSL,
  MAIL_USERNAME, MAIL_PASSWORD, MAIL_FROM, MAIL_BASE_URL,
  MAIL_TIMEOUT, MAIL_RETRIES (선택)


================================================================================
16. 최고기술경영실 개입 · 자동감지 · 정확도
================================================================================

16.1 에스컬레이션
  normal     일반
  monitor    모니터링          ← 「최고기술경영실 보고」 시 자동
  critical   심각·개입중       ← C가 지정
  cleared    문제없음(검토완료) ← 조치 없이 검토 종료. 자동감지·심각카드에서 제외
  closed     종결              ← 조치 있었던 종료

개입상태 intervention_status: none / watching / intervening / done
보상상태: none / reviewing / negotiating / agreed / dispute
보상금액·메모, intervention_log 누적.

편집은 C만. AS보고서 「최고기술경영실 개입 현황」은 전 직원 조회.
C 로그인 시 보고서에 「개입 관리」 버튼 → /tech-monitor?focus=

홈 최상단: 심각·모니터링 대상 카드.
C 전용: 자동 감지 정확도 통계 카드 (E·X 포함 타권한 절대 비노출).

16.2 자동감지 규칙 (AUTO_FLAG_RULES in v32_rules.py)
  repeat_equipment     동일 고객+장비 누적 접수 ≥ 3
  high_billing         청구비용 ≥ 3,000,000 또는 부품합계 ≥ 2,000,000
  long_open_days       접수 후 14일+ & 미완료
  many_parts_kinds     부품 종류 ≥ 4
  repeat_customer      최근 90일 동일 고객 ≥ 3건
  rod_mass             용접봉 전용: 교체 중량 합 ≥ 1000kg

동작:
- 강제 에스컬레이션하지 않음. 「검토 필요 후보」만.
- 등급이 monitor/critical/closed 면 후보 카드에서 제외(심각 카드로).
- cleared 면 양쪽 제외. 「지금까지 검토완료(문제없음) N건」 카운트.
- normal 유지 시 조건이 같으면 재노출 (의도된 설계).

16.3 정확도 통계 (C 홈만)
  누적 자동감지 = 규칙 1개라도 해당하는 현재 건수 (검토 여부 무관)
  실제 문제 확인 = 그중 monitor/critical/closed
  오탐 = cleared
  정확도 = 확인 / (확인+오탐) * 100
  미검토(normal)는 분모 제외. 검토 0건이면 정확도 「—」 (0%와 구분).
  현재 데이터 기준 매번 재계산 (스냅샷 이력 없음).


================================================================================
17. 제품군 확장 — 용접봉
================================================================================

17.1 게이트
- 첫 화면: 웰딩 장비 / 용접봉 카드.
- 헤더 「제품군 변경」.
- 데이터·집계 완전 분리.

17.2 라벨
화면항목              웰딩 장비              용접봉
제품                  장비명                 제품명 + 규격(필수) + 로트(선택)
시리얼                직접입력               제품명·규격·로트 자동표시 읽기전용
이력 3단계            점검 및 수리 내용      점검 및 조치 내용
수리/조치             수리내용               조치내용
4단계                 교체 부품              제품 교환
표 컬럼 수량          수량                   수량(KG)
AS상태                5단계(+부품대기)       4단계(부품대기 없음)
지원 기술             기술부에 공유          기술연구소에 공유
보고서                장비명·시리얼·수리·부품  제품명·식별정보·조치·제품교환

ui_labels() 로 템플릿에 주입.

17.3 AS 대응팀 드롭다운
- 제품군 선택 직후(로그인 전)부터 올바른 목록.
  welding: 내수·관계사·수출 (권한에 따라 일부만 선택/고정)
  rods: AS담당관만, 선택 불가(고정)
- 이력 소속 드롭다운:
  welding: 내수영업팀/관계사영업팀/수출영업팀, 기본값 내수영업팀
  rods: AS담당관/기술연구소, 기본값 AS담당관
- 용접봉 접수담당 후보에 sales_head 넣지 말 것.

17.4 사무소 공용계정
문제: 접수담당자=개인이름 이라 공용계정 재로그인 시 「내 접수」가 비고, 완료알림도 유실.
해결:
  receipts.office 자동 기록
  홈 필터: 사무소계정 → office 일치, 카드제목 「우리 사무소 접수 AS 목록」
           일반직원 → receipt_staff / 이름 기준
  완료알림: office 있으면 team_code=office person.id (toType team)
           없으면 개인 user_code

17.5 정직원 담당 사무소
- _can_pick_assigned_office: 사무소 공용계정 제외.
  admin, 부서장, 또는 team_code in (domestic, affiliate, export)
- 저장값: 사무소명 문자열 또는 '' (해당 없음)
- 허용 목록: SALES_OFFICES 의 name 13개만

17.6 드롭다운 제품군
- 소속에 AS담당관·기술연구소 옵션 존재
- 필터 시점을 로그인이 아니라 제품군 선택 시점으로
- 숨겨진 첫 option이 기본 선택되지 않게 기본값을 명시 지정
- 용접봉 접수담당에 부서장 제외


================================================================================
18. 화면별 구현 명세
================================================================================

18.1 base.html
- viewport-fit=cover, theme-color 마룬.
- 로고: HYUNDAI WELDING + 시스템명(웰딩 장비 AS / 용접봉 AS).
- 제품군 변경 버튼.
- 네비: 권한별 탭. rods는 /rods, /rods/sales, /rods/history, /rods/report 엔드포인트
  (내부적으로 product_line 세팅 후 /sales 등으로 redirect, 쿼리 유지).
- .nav-link 줄바꿈 금지 (white-space:nowrap).
- 모바일 햄버거 #navToggle / #siteNav.
- 알림벨 + 배지.
- 로그인 모달 (ID/PW + hidden login_nonce). 용접봉은 「로그인 (담당자 선택)」 문구 가능.
- 비밀번호 변경 모달 POST /account/password (csrf, 4자 이상).
- 모든 페이지는 base.html 확장.

18.2 홈 index.html
역할별 블록:
  C: 개입·보상 / 현황보고 바로가기
     심각·모니터링 카드, 자동감지 후보 카드, 정확도 통계(C만),
     전사 목록은 lazy API (/api/home/team-receipts, /api/home/tech-cards)
  E / X: 현황보고 + 최근 접수 현황(실데이터, 미완료 우선, 상위 약 25건)
     KPI: 금일/7일/당월 접수, 미처리(진행+부품대기·보류), 7일 완료
  일반: 신규접수 / 이력 / 보고서 바로가기 (권한 있는 것만)
     KPI 4칸: 금일 신규, 진행중(부품대기·보류 세부), 이번달 완료, 완료율(+재수리 건수 가능)
     팀 AS 목록, 내(또는 우리 사무소) 접수 목록+대기순번, 내 AS 처리 이력
검색·상태·AS 대응팀 필터 (옵션 「전체 팀」은 유지),
placeholder 「접수번호·고객·장비·증상·AS 대응팀」
홈 CSV (/api/home/export.csv?kind=team|my-receipts|my-history)
메일 열: 주소 없음. 「발송」 또는 「발송됨」.
모바일 카드 라벨 CSS ::before 도 「AS 대응팀」.

18.3 영업접수 sales.html
- 등록 폼: 접수일, AS 대응팀(제품군·권한 필터), 접수담당,
  사무소면 접수담당 이름 직접입력 + office 자동,
  정직원이면 담당 사무소(선택),
  고객/연락처/장비or제품/규격·로트/출하일/증상/유무상/사진/AS지정/지원요청
- 목록: 미완료 우선, 대기순번, 상태, 유무상 전환, 가격책정, 부품관리,
  지정·변경(권한자만), 수정·삭제(등록자·현재접수담당·부서장·admin)
- JS: 팀 변경 시 접수담당·AS담당 옵션 필터 (data-team-code).
  수출 비부서장: AS 후보 장호빈·김윤식만, 기본 장호빈.
  수출 정규직 선택 시 AS 라벨/hidden 전환 (김윤식 ↔ 미지정).
- 과거이력 패널.

18.4 이력관리 history.html
- 팀 탭 + 대기 건수. 드롭다운 최신순, 신규 건 자동선택.
- 완료 건 로드 시 수정 불가 안내 후 차단.
- 점검정보 5필드(점검날짜·소속·서비스담당자=로그인자동·처리위치·출고일) 저장·복원.
- 부품 행은 실제 tbody에 append (브라우저 암시 tbody 버그 주의).
- 사진 태그별 저장·복원.
- 지원요청·최고기술경영실 보고 체크 + 즉시 공유 버튼.
- 청구 패널 (마진/직접입력).
- 고객정보 박스에 「마지막 수정 {시각} · {이름}」 (없으면 「아직 이력 저장 없음」).
- hidden name=history_updated_at. 불러오기/공유 성공 시 갱신.
- 충돌 시 서버 flash: 다른 담당자가 먼저 저장·공유함. 「불러오기」 다시.
  공유 API 충돌은 alert(같은 문구).
- 보고서 왕복 시 sessionStorage 임시입력 복원. 잠금 토큰은 서버 로드 값을 유지(드래프트에 넣지 않음).

18.5 AS보고서
- 전체 건 조회 (임시저장·진행중 포함).
- last_worked_receipt_no 세션으로 탭 직접 진입 시 최근 작업 건 표시.
- 인쇄 = window.print, PDF = /report/<no>/pdf
- return_url 상대경로만 허용. /rods/history, /rods/sales 도 「돌아가기」 라벨 처리.
- undefined/NaN 노출 금지.

18.6 현황보고 status_report.html
- 접근: E, C, X, admin (_executive_required 에 sales_head·tech_mgmt 포함)
- period= month | quarter | year, year 선택
- 「AS 대응팀별 요약」 표 첫 열 헤더 「AS 대응팀」
- 팀별·기간별 집계, 전체 이력 표, CSV

18.7 개입·보상 tech_monitor.html
- C 전용. 필터(등급·AS 대응팀·상태·검색), 개입 폼, 자동감지 후보, 조치이력 추가.
- 목록 열 「AS 대응팀」. 필터 옵션 「전체 팀」 유지.

18.8 admin
- 부서 CRUD, 인원 CRUD(다중 email, can_receipt, can_as, team), 로그인 계정 CRUD.
- 인원 저장 시 users 동기화.
- 메일 UI: 입력 여러 칸 + 「+ 메일 추가」. form name=emails.


================================================================================
18.9 이력관리 동시 저장 (구현 알고리즘)
================================================================================

토큰 = detail.history_updated_at (없으면 '')

불러오기 GET /api/history/<no>
  payload에 history_updated_at, history_updated_by, history_updated_display 포함.

저장 POST /history/save  (완료 잠금 직후, 사진 업로드 전)
  submitted = form history_updated_at  (없으면 '')
  if current != submitted:
    flash 충돌 메시지(마지막 수정 시각·이름 포함) 후 해당 건으로 redirect.
  통과 후 본문 저장, _stamp_history_modified(detail, person), _save_service_data.

공유 POST /api/support-share
  JSON에 history_updated_at 키가 있으면 검사. 없으면 검사 생략(영업접수).
  already 이면 검사·스탬프 생략.
  실제 변경 시 스탬프 후 응답에 새 토큰. 클라이언트 applyHistoryLock.
  이력 화면은 항상 키를 보내므로 타인 저장 후 공유/저장 모두 차단.

품질팀 share_tech 폼: form 토큰으로 동일 검사.

영업접수 공유가 시각을 갱신하면, 열린 이력 폼의 이후 저장은 충돌 → 불러오기 필요.
이것은 의도된 동작이다.

본인 공유 후 저장이 막히지 않는 이유:
  공유 성공 시 폼 hidden 토큰을 새 값으로 갈아끼움. 「수정자가 나라서 통과」 예외는 쓰지 않음.
  (예외를 쓰면 타인 저장 → 본인 공유 → 본인 저장이 타인 점검내용을 덮어씀)


================================================================================
19. 라우트 목록 (재구현 시 경로 유지)
================================================================================

페이지:
  GET  /                            제품군 선택 (session.clear)
  GET  /home                        웰딩 홈
  GET  /rods                        용접봉 홈
  GET  /rods/sales|/history|/report 용접봉 진입 후 본 화면 redirect
  GET  /switch-product              session.clear → /
  GET  /sales  POST /sales/register  POST /sales/<no>/update  POST /sales/<no>/delete
  GET  /history  POST /history/save
  GET  /report  GET /report/<no>/pdf
  GET  /status-report  GET /status-report/export.csv
  GET  /tech-monitor  POST /tech-monitor/<no>/update
  POST /login  GET /logout
  POST /account/password
  GET  /admin/users  + org/users CRUD POST들
  POST /receipt/<no>/send-email     csrf, notice_sent 가드

API:
  GET  /api/receipt-no-preview
  GET  /api/history/list
  GET  /api/history/<no>
  GET  /api/report/<no>
  GET  /api/past-history
  GET  /api/re-repair-suggest
  GET  /api/warranty
  GET  /api/notifications
  POST /api/notifications/<id>/read
  POST /api/notifications/read-all
  POST /api/sales/billing
  POST /api/sales/charge-type
  GET|POST /api/sales/parts
  POST /api/sales/assign-as          _can_reassign_as_staff
  POST /api/support-share
  GET  /api/session-status           {user_id, username, user_name}
  GET  /api/tech/escalations
  GET  /api/home/team-receipts       notice_sent, can_send_email (이메일 문자열 없음)
  GET  /api/home/tech-cards
  GET  /api/home/export.csv


================================================================================
20. CSV
================================================================================

20.1 현황보고 전체 이력 (HISTORY_CSV_FIELDS, utf-8-sig)
컬럼 순서 고정 (실제 writer fieldnames. extrasaction=ignore):

접수번호, 접수연도, 접수분기, 접수월, 접수일,
완료연도, 완료분기, 완료월, 완료일,
점검연도, 점적분기, 점검월, 점검일,
팀코드, 팀명, 고객명, 고객담당자, 연락처, 장비명, 시리얼번호, 증상,
유무상, 유무상코드, 청구비용, 청구마진율, 청구수동책정, 청구책정자,
부품건수, 부품합계금액, 부품내역,
워런티코드, 워런티, 워런티경과일, 출하일, 재수리여부,
AS상태코드, AS상태, 접수담당자, 접수사무소, AS배정담당자, AS처리담당자,
소속, 처리위치, 출고일, 점검결과, 수리내용, 상세작업내용, 비고,
지원요청이력, 품질지원요청, 기술지원요청, 최고기술경영실보고,
에스컬레이션, 개입상태, 보상상태, 보상금액,
고객전달사진수, 현장사진수, 보고서작성일, 등록일시

행수 = 해당 제품군 접수 건수 (필터 연도 적용 시 해당 연도).
※ 행 dict에는 규격/로트번호/제품군을 만들지만 fieldnames에 없어 파일에는 안 나간다.
   재구현 시 현재와 동일하게 헤더는 위 목록만.

20.2 홈 CSV
kind=team:
  접수번호, 접수일, AS 대응팀, 고객명, 장비명, 증상, 담당자, 상태
kind=my-receipts:
  접수번호, 접수일, 고객명, 장비명, 증상, AS 대응팀, 접수담당자, 접수사무소, 대기순번, 상태
kind=my-history:
  접수번호, 접수일, 고객명, 장비명, 증상, AS 대응팀, 상태
경영진·최고기술은 my-receipts/my-history 내보내기 없음(부서장은 본인 접수 가능).


================================================================================
21. UI · CI · 반응형 (필수)
================================================================================

- CI: 마룬 계열 (#7a1f3d / #7a1f1a), 카드형 리스트, 기존 PNS 웹과 톤 맞춤.
- 모든 새 HTML은 base.html 확장.
- 고정 min-width 1000px+ 금지 (표는 .table-wrap 가로스크롤).
- 컨테이너 max-width:100%, min-width:0.
- @media max-width 1024 / 768 에서 카드·필터·폼 1열.
- 버튼·탭·입력 최소 높이 ~44px, touch-action: manipulation.
- 모달 width: min(..., calc(100vw - 24px)), max-height: calc(100dvh - …).
- 이미지 max-width:100%; height:auto.
- 핵심 기능은 호버 전용이면 안 됨 (터치 탭).
- 확인 폭: 375 / 768 / 1280.
- 헤더 메뉴 항목은 한 줄 유지(nowrap). 넘치면 기존 햄버거/가로스크롤 패턴.


================================================================================
22. 모듈 책임 (재구현 시 파일 분리 권장)
================================================================================

app.py
  Flask 앱, 스키마, 시드, RECEIPTS 로드/세이브, 페이지 라우트,
  대시보드·현황보고·CSV·보고서 빌드, 로그인/admin,
  접수담당/AS대응팀/사무소/OCC/다중메일/notice_sent 헬퍼.

v32_rules.py
  AUTO_FLAG_RULES, ESCALATION_*, DEFAULT_MARGIN_RATE=30,
  warranty_verdict, suggest_re_repair, calc_parts_total,
  calc_billing_from_margin, auto_detect_result, incomplete_first_key,
  status_label, normalize_escalation. Flask 의존 금지.

v32_app_ext.install(app_module)
  _can_access_sales/history, _is_sales_head, export 라우팅 오버라이드,
  _waiting_receipts, _notify_export_register, _push_notification,
  _can_edit_sales_ops, 자동감지 카드 빌드, 상기 API 라우트 등록.
  assign-as 는 _can_reassign_as_staff 사용.

product_line_ext.py
  SESSION_WELDING/RODS, PL_EQUIPMENT/ROD, SALES_OFFICES, 가시성,
  ui_labels, status_options, ensure_schema_and_org.

email_notify.py / pdf_report.py
  메일·PDF만. 메일은 다중 수신.


================================================================================
23. 세션 키
================================================================================

user_id, username, user_name, user_role, csrf_token,
product_line ('welding'|'rods'),
last_worked_receipt_no,
login_nonce   (로그인 폼 1회용. 성공/실패/만료 시 재발급)


================================================================================
24. 알려진 제약 (버그 아님)
================================================================================

- 고객·장비 매칭은 문자열 완전 일치. 마스터 ID/시리얼 기반은 미구현.
- 로그인은 SSO 아님 (ID/PW, 실무자 기본 1234).
- 담당 사전지정은 강제 배정이 아니라 알림+표시. 저장자가 self-assign.
- 재수리 365일·마진 30%는 정책값. 상수로 둘 것.
- 최고기술경영실의 그룹사 전반 연동 API는 본 시스템 범위 밖.
  다만 GET /api/tech/escalations 는 연동 대비 조회 API로 제공.
- 자동감지 「일반」 유지는 재노출이 의도된 동작.
- 완료 건 이력 재편집 불가는 의도된 정책.
- 화면 「AS 대응팀」과 DB team_id 불일치는 의도. 필드명 바꾸지 말 것.
- 같은 사용자가 두 탭으로 열어 한쪽만 저장하면 다른 탭 저장은 충돌. 의도됨.
- 홈 안내메일은 주소 비공개·1회. 의도됨.


================================================================================
25. 재구현 체크리스트 (이 파일이면 충분한 합격 기준)
================================================================================

[게이트] 웰딩/용접봉 선택, 변경 시 로그아웃, 데이터 비혼합
[로그인] admin/admin, 최영호 등 이름/1234, 제품군별 목록, nonce, 홈 진입 시 성공 flash 없음
[메뉴] I/A/IA/X/E/D/C 매트릭스대로 탭·카드 숨김, 헤더 줄바꿈 없음
[접수번호] HWDS-/RD- 일자순번, 수정불가
[용어] 화면 전부 「AS 대응팀」, DB는 team_id
[접수담당] 일반=본인고정, 부서장=3팀선택, 사무소=이름입력
[사무소] 공용=자동, 정직원=선택(해당없음 가능)
[수출선택] 김윤식·이명지: 수출→김윤식, 내수→미지정. 내수/관계사 드롭다운 숨김
[수출기본] 기본 장호빈, 최영호 이력 비노출, 김윤식 처리 가능
[AS지정] 팀장·부서장·admin만 「지정·변경」. 팀원은 유무상·부품만
[상태] 임시저장 vs 완료 버튼, 완료 잠금, 보고서 전건 조회
[순번] 지정/미지정/완료됨 표시, 미지정 문구에 AS 대응팀
[팀목록] [수출영업팀접수] 태그
[메일UI] 주소 숨김, 발송/발송됨, notice_sent 1회
[다중메일] 계정관리 여러 주소, 일괄 발송
[지원] 품질/기술(연구소) 필터, 메일+인앱, 즉시공유 OCC
[유무상·청구·부품] 마진 30/0 구분, 수동책정 유지, 영업부품팝업
[재수리·워런티·과거이력] + 보고서 왕복
[알림벨] 개인/팀/사무소 매칭, 클릭 라우팅, 전체읽음
[OCC] 두 사람이 같은 건 저장 시 후저장 거부, 본인 공유 후 저장 가능
[C] 개입 모달, 보고서 개입버튼, 자동감지 5(+1톤), 정확도 —/퍼센트, 타권한 비노출
[사무소홈] office 필드, 우리 사무소 목록, 완료 팀알림
[드롭다운] 제품군 전환 즉시 AS 대응팀/소속/접수담당 후보 정상
[CSV] 현황보고 헤더=20.1, 홈 CSV 헤더에 AS 대응팀, BOM, 이메일 없음
[반응형] 375/768/1280, 햄버거, table-wrap
[QA] python qa_verify.py 전항 통과 수준


================================================================================
끝.
================================================================================
