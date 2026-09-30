"""AS 보고서 PDF 생성"""

from io import BytesIO
from pathlib import Path

from fpdf import FPDF


FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\malgun.ttf"),
    Path(r"C:\Windows\Fonts\malgunbd.ttf"),
    Path(r"C:\Windows\Fonts\NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
]


def _find_korean_font():
    for path in FONT_CANDIDATES:
        if path.exists():
            return str(path)
    return None


class ReportPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=15)
        font_path = _find_korean_font()
        if not font_path:
            raise RuntimeError("한글 폰트를 찾을 수 없습니다. (malgun.ttf 등)")
        self.add_font("KR", "", font_path)
        self.add_font("KR", "B", font_path)
        self.set_margins(15, 15, 15)

    def section_title(self, title):
        self.set_fill_color(253, 242, 244)
        self.set_text_color(122, 31, 61)
        self.set_font("KR", "B", 11)
        self.cell(0, 8, title, border=1, new_x="LMARGIN", new_y="NEXT", fill=True)
        self.set_text_color(26, 26, 26)

    def text_box(self, text, min_h=12):
        self.set_font("KR", "", 10)
        start_y = self.get_y()
        self.multi_cell(0, 6, text or "—", border=1)
        if self.get_y() - start_y < min_h:
            # 최소 높이 보장
            pass

    def kv_row(self, pairs, col_w):
        """pairs: [(label, value), ...] 한 행에 최대 3개"""
        self.set_font("KR", "", 9)
        x0 = self.l_margin
        y0 = self.get_y()
        max_h = 0
        for i, (label, value) in enumerate(pairs):
            x = x0 + i * col_w
            self.set_xy(x, y0)
            self.set_fill_color(250, 250, 250)
            self.set_text_color(107, 114, 128)
            self.cell(col_w, 5, str(label), border="LTR", fill=True)
            self.set_xy(x, y0 + 5)
            self.set_text_color(26, 26, 26)
            self.multi_cell(col_w, 5, str(value if value not in (None, "") else "—"), border="LBR")
            max_h = max(max_h, self.get_y() - y0)
        self.set_y(y0 + max_h)


def _url_to_path(url, base_dir: Path):
    """/static/uploads/... → 로컬 파일 경로"""
    if not url:
        return None
    if url.startswith("/static/"):
        rel = url[len("/static/") :]
        path = base_dir / "static" / rel
        return path if path.exists() else None
    if url.startswith("static/"):
        path = base_dir / url
        return path if path.exists() else None
    return None


def build_report_pdf(report: dict, base_dir: Path) -> bytes:
    pdf = ReportPDF()
    pdf.add_page()
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    col = usable / 3
    labels = report.get("ui_labels") or {}
    title = labels.get("report_title") or "용접기 및 용접장비 수리보고서"
    product_field = labels.get("product_field") or "장비명"
    serial_field = labels.get("report_serial") or "시리얼넘버"
    repair_title = labels.get("report_repair") or "수리 내용"
    parts_title = labels.get("report_parts") or "교체 부품"
    part_name = labels.get("part_name") or "부품명"
    part_qty = labels.get("part_qty") or "수량"
    parts_total_label = labels.get("parts_total") or "부품 합계금액"

    # 제목
    pdf.set_font("KR", "B", 16)
    pdf.cell(0, 12, title, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # 메타
    pdf.set_font("KR", "", 10)
    meta = (
        f"문서번호: {report.get('receipt_no', '—')}    "
        f"작성일자: {report.get('report_date', '—')}    "
        f"수리유형: {report.get('charge_label', '—')}    "
        f"AS 상태: {report.get('status_label', '—')}"
    )
    pdf.set_text_color(122, 31, 61)
    pdf.multi_cell(0, 6, meta, border=1)
    pdf.set_text_color(26, 26, 26)
    pdf.ln(3)

    # 고객 정보
    pdf.section_title("고객 정보")
    rows = [
        [("고객사명", report.get("customer_name")), ("담당자", report.get("customer_contact")), ("연락처", report.get("phone"))],
        [(product_field, report.get("equipment_name")), (serial_field, report.get("serial_no")), ("재수리 여부", report.get("re_repair"))],
        [("접수 담당자", report.get("receipt_staff_name")), ("AS 담당자", report.get("as_technician")), ("최초 출하일/워런티", report.get("ship_warranty"))],
        [("점검날짜", report.get("inspection_date")), ("소속", report.get("affiliation")),
         ("처리위치/출고일자", f"{report.get('process_location', '—')} / {report.get('ship_out_date', '—')}")],
    ]
    for row in rows:
        pdf.kv_row(row, col)
    pdf.ln(3)

    pdf.section_title("증상 / 점검결과")
    pdf.text_box(report.get("symptom_text") or "—")
    pdf.ln(2)

    pdf.section_title(repair_title)
    pdf.text_box(report.get("repair_content") or "—")
    pdf.ln(2)

    pdf.section_title("상세 점검 및 작업내용")
    pdf.text_box(report.get("detailed_work") or "—", min_h=16)
    pdf.ln(2)

    # 교체 부품 / 제품 교환
    pdf.section_title(parts_title)
    pdf.set_font("KR", "B", 9)
    pdf.set_fill_color(250, 250, 250)
    widths = [usable * 0.28, usable * 0.18, usable * 0.14, usable * 0.2, usable * 0.2]
    headers = [part_name, "규격", part_qty, "단가", "소계"]
    for w, h in zip(widths, headers):
        pdf.cell(w, 7, h, border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_font("KR", "", 9)
    parts = report.get("parts") or []
    if not parts:
        pdf.cell(0, 7, "등록된 항목 없음", border=1, align="C", new_x="LMARGIN", new_y="NEXT")
    else:
        for p in parts:
            pdf.cell(widths[0], 7, str(p.get("name", "")), border=1)
            pdf.cell(widths[1], 7, str(p.get("spec") or "—"), border=1)
            pdf.cell(widths[2], 7, str(p.get("qty", 0)), border=1, align="C")
            pdf.cell(widths[3], 7, f"{int(p.get('unit_price', 0)):,}원", border=1, align="R")
            pdf.cell(widths[4], 7, f"{int(p.get('subtotal', 0)):,}원", border=1, align="R")
            pdf.ln()
    pdf.set_font("KR", "B", 9)
    pdf.cell(0, 7, f"{parts_total_label} {int(report.get('parts_total') or 0):,}원", border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.section_title("청구 비용")
    pdf.set_font("KR", "", 10)
    charge = f"[{report.get('charge_label', '—')}] {report.get('billing_note', '')}"
    pdf.multi_cell(0, 6, charge, border=1)
    pdf.ln(2)

    pdf.section_title("지원 요청 이력")
    pdf.text_box(report.get("support_history") or "지원 요청 이력 없음")
    pdf.ln(2)

    # 사진
    pdf.section_title("현장 사진 / 고객 전달 사진")
    photos = report.get("photo_items") or []
    if not photos:
        # URL 목록만 있는 경우
        for url in report.get("photos") or []:
            photos.append({"url": url, "tag": ""})

    if not photos:
        pdf.set_font("KR", "", 10)
        pdf.cell(0, 10, "첨부 사진 없음", border=1, align="C", new_x="LMARGIN", new_y="NEXT")
    else:
        tag_label = {"customer": "고객 전달", "before": "수리 전", "after": "수리 후", "parts": "교체 부품"}
        x = pdf.l_margin
        y = pdf.get_y() + 2
        img_w = 55
        img_h = 45
        gap = 5
        count_in_row = 0
        for item in photos:
            url = item.get("url") if isinstance(item, dict) else item
            path = _url_to_path(url, base_dir)
            if not path:
                continue
            if y + img_h + 10 > pdf.h - pdf.b_margin:
                pdf.add_page()
                y = pdf.get_y()
                x = pdf.l_margin
                count_in_row = 0
            try:
                pdf.image(str(path), x=x, y=y, w=img_w, h=img_h)
                pdf.set_xy(x, y + img_h)
                pdf.set_font("KR", "", 8)
                label = tag_label.get((item.get("tag") if isinstance(item, dict) else "") or "", "사진")
                pdf.cell(img_w, 5, label, align="C")
            except Exception:
                continue
            count_in_row += 1
            if count_in_row >= 3:
                y += img_h + 10
                x = pdf.l_margin
                count_in_row = 0
                pdf.set_y(y)
            else:
                x += img_w + gap
        if count_in_row:
            pdf.set_y(y + img_h + 10)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()
