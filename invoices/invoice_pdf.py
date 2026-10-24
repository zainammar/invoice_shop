"""
Invoice PDF builder  ->  save as  invoices/invoice_pdf.py

Usage in views.py:
    from .invoice_pdf import build_invoice_pdf
    pdf_bytes = build_invoice_pdf(invoice, request.user)
"""
from decimal import Decimal
from functools import partial
import io
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ── Palette ──────────────────────────────────────────────────
INK = colors.HexColor('#0f172a')
ACCENT = colors.HexColor('#4f46e5')
MUTED = colors.HexColor('#64748b')
LINE = colors.HexColor('#e2e8f0')
ZEBRA = colors.HexColor('#f8fafc')
RED = colors.HexColor('#b91c1c')
WHITE = colors.white

W, H = A4
M = 16 * mm            # page side margin
CW = W - 2 * M         # content width
FIRST_HEADER_H = 46 * mm
LATER_HEADER_H = 18 * mm
FOOTER_H = 20 * mm


# ── Small helpers ────────────────────────────────────────────
def money(value, decimals=False):
    v = Decimal(str(value or 0))
    return f'Rs. {v:,.2f}' if decimals else f'Rs. {v:,.0f}'


def percent_text(value):
    return f'{float(value):g}%'


def fit(text, font, size, max_width):
    """Text ko max_width mein fit karta hai (zarurat ho to ... laga deta hai)."""
    text = text or ''
    if stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text + '...', font, size) > max_width:
        text = text[:-1]
    return text + '...'


def para_text(value):
    return escape(value or '').replace('\n', '<br/>')


def style(name, **kw):
    base = dict(fontName='Helvetica', fontSize=9, leading=12, textColor=INK)
    base.update(kw)
    return ParagraphStyle(name, **base)


ST = {
    'label': style('label', fontName='Helvetica-Bold', fontSize=7.5, leading=10, textColor=ACCENT),
    'cust': style('cust', fontName='Helvetica-Bold', fontSize=12, leading=16),
    'small': style('small', fontSize=8.5, leading=12, textColor=MUTED),
    'meta_l': style('meta_l', fontSize=8.5, textColor=MUTED),
    'meta_v': style('meta_v', fontName='Helvetica-Bold', fontSize=8.5, alignment=TA_RIGHT),
    'meta_red': style('meta_red', fontName='Helvetica-Bold', fontSize=8.5, alignment=TA_RIGHT, textColor=RED),
    'th': style('th', fontName='Helvetica-Bold', fontSize=7.5, leading=10, textColor=WHITE),
    'th_c': style('th_c', fontName='Helvetica-Bold', fontSize=7.5, leading=10, textColor=WHITE, alignment=TA_CENTER),
    'th_r': style('th_r', fontName='Helvetica-Bold', fontSize=7.5, leading=10, textColor=WHITE, alignment=TA_RIGHT),
    'td': style('td'),
    'td_b': style('td_b', fontName='Helvetica-Bold'),
    'td_m': style('td_m', textColor=MUTED, alignment=TA_CENTER),
    'td_c': style('td_c', alignment=TA_CENTER),
    'td_r': style('td_r', alignment=TA_RIGHT),
    'td_amt': style('td_amt', fontName='Helvetica-Bold', alignment=TA_RIGHT, textColor=ACCENT),
    'tot_l': style('tot_l', textColor=MUTED),
    'tot_v': style('tot_v', fontName='Helvetica-Bold', alignment=TA_RIGHT),
    'tot_red': style('tot_red', fontName='Helvetica-Bold', alignment=TA_RIGHT, textColor=RED),
    'grand_l': style('grand_l', fontName='Helvetica-Bold', fontSize=10, leading=14, textColor=WHITE),
    'grand_v': style('grand_v', fontName='Helvetica-Bold', fontSize=13, leading=16, textColor=WHITE, alignment=TA_RIGHT),
    'note': style('note', fontSize=8.5, leading=12, textColor=MUTED),
}


class NumberedCanvas(canvas.Canvas):
    """Har page par footer + 'Page X of Y'."""

    def __init__(self, *args, footer_left='', **kwargs):
        super().__init__(*args, **kwargs)
        self._footer_left = footer_left
        self._saved_states = []

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total):
        self.saveState()
        self.setStrokeColor(LINE)
        self.setLineWidth(0.6)
        self.line(M, 14 * mm, W - M, 14 * mm)
        self.setFillColor(MUTED)
        self.setFont('Helvetica', 7.5)
        self.drawString(M, 9.5 * mm, self._footer_left)
        self.drawRightString(W - M, 9.5 * mm, f'Page {self._pageNumber} of {total}')
        self.restoreState()


# ── Main builder ─────────────────────────────────────────────
def build_invoice_pdf(invoice, user):
    # Company profile (optional)
    try:
        profile = user.company
    except Exception:
        profile = None

    company_name = (getattr(profile, 'company_name', '') or user.username)
    company_address = (getattr(profile, 'address', '') or '').replace('\n', ' ')
    company_phone = getattr(profile, 'phone', '') or ''
    company_email = getattr(profile, 'email', '') or ''

    logo_path = avatar_path = None
    try:
        if profile and profile.company_logo:
            logo_path = profile.company_logo.path
    except Exception:
        pass
    try:
        if profile and profile.user_avatar:
            avatar_path = profile.user_avatar.path
    except Exception:
        pass

    issue_date = timezone.localtime(invoice.created_at).strftime('%d %b %Y')
    items = list(invoice.items.all())
    subtotal = invoice.subtotal()
    discount = invoice.discount_amount()
    total = invoice.total_amount()

    # Poore invoice mein ek hi format: koi bhi amount fractional ho to sab 2 decimals
    money_values = [subtotal, discount, total] + [i.amount for i in items] + [i.unit_price for i in items]
    use_dec = any(Decimal(str(v)) != Decimal(str(v)).to_integral_value() for v in money_values)
    fmt = partial(money, decimals=use_dec)

    # ── Page decorations ─────────────────────────────────────
    def draw_first_page(c, doc):
        c.saveState()

        # top accent strip
        c.setFillColor(ACCENT)
        c.rect(0, H - 3 * mm, W, 3 * mm, fill=1, stroke=0)

        # logo (or initials placeholder)
        LOGO = 18 * mm
        lx, ly = M, H - 10 * mm - LOGO
        drawn = False
        if logo_path:
            try:
                c.drawImage(logo_path, lx, ly, width=LOGO, height=LOGO,
                            preserveAspectRatio=True, anchor='c', mask='auto')
                drawn = True
            except Exception:
                drawn = False
        if not drawn:
            c.setFillColor(ACCENT)
            c.roundRect(lx, ly, LOGO, LOGO, 3 * mm, stroke=0, fill=1)
            c.setFillColor(WHITE)
            c.setFont('Helvetica-Bold', 14)
            c.drawCentredString(lx + LOGO / 2, ly + LOGO / 2 - 5, company_name[:2].upper())

        # company text
        tx = M + LOGO + 5 * mm
        avail = W - M - 62 * mm - tx
        c.setFillColor(INK)
        c.setFont('Helvetica-Bold', 15)
        c.drawString(tx, H - 15 * mm, fit(company_name, 'Helvetica-Bold', 15, avail))

        c.setFillColor(MUTED)
        c.setFont('Helvetica', 8.5)
        y = H - 21 * mm
        for line in simpleSplit(company_address, 'Helvetica', 8.5, avail)[:2]:
            c.drawString(tx, y, line)
            y -= 4.2 * mm
        contact = '  |  '.join(p for p in (company_phone, company_email) if p)
        if contact:
            c.drawString(tx, y, fit(contact, 'Helvetica', 8.5, avail))

        # right block
        rx = W - M
        c.setFillColor(ACCENT)
        c.setFont('Helvetica-Bold', 26)
        c.drawRightString(rx, H - 17 * mm, 'INVOICE')
        c.setFillColor(INK)
        c.setFont('Helvetica-Bold', 10)
        c.drawRightString(rx, H - 24 * mm, invoice.invoice_number)
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 8.5)
        c.drawRightString(rx, H - 29 * mm, f'Issued: {issue_date}')

        # prepared-by chip (sirf jab avatar ho)
        if avatar_path:
            try:
                r = 3 * mm
                cx, cy = M + r, H - 34.5 * mm
                c.saveState()
                p = c.beginPath()
                p.circle(cx, cy, r)
                c.clipPath(p, stroke=0)
                c.drawImage(avatar_path, cx - r, cy - r, width=2 * r, height=2 * r,
                            preserveAspectRatio=True, anchor='c', mask='auto')
                c.restoreState()
                c.setFillColor(MUTED)
                c.setFont('Helvetica', 7.5)
                c.drawString(M + 2 * r + 2 * mm, cy - 1 * mm, f'Prepared by {user.username}')
            except Exception:
                pass

        # separator
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(M, H - 41 * mm, W - M, H - 41 * mm)
        c.restoreState()

    def draw_later_page(c, doc):
        c.saveState()
        c.setFillColor(ACCENT)
        c.rect(0, H - 3 * mm, W, 3 * mm, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(M, H - 10 * mm, fit(company_name, 'Helvetica-Bold', 9, CW * 0.6))
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 8.5)
        c.drawRightString(W - M, H - 10 * mm, f'Invoice {invoice.invoice_number}')
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(M, H - 14 * mm, W - M, H - 14 * mm)
        c.restoreState()

    # ── Story ────────────────────────────────────────────────
    story = [NextPageTemplate('later')]

    # 1) Bill-to + meta cards
    bill = [Paragraph('BILL TO', ST['label']), Spacer(1, 2),
            Paragraph(para_text(invoice.customer_name), ST['cust'])]
    if invoice.customer_email:
        bill.append(Paragraph(para_text(invoice.customer_email), ST['small']))
    if invoice.customer_address:
        bill.append(Paragraph(para_text(invoice.customer_address), ST['small']))

    meta_rows = [
        [Paragraph('Invoice No.', ST['meta_l']), Paragraph(para_text(invoice.invoice_number), ST['meta_v'])],
        [Paragraph('Issue date', ST['meta_l']), Paragraph(issue_date, ST['meta_v'])],
        [Paragraph('Prepared by', ST['meta_l']), Paragraph(para_text(user.username), ST['meta_v'])],
        [Paragraph('Status', ST['meta_l']), Paragraph('UNPAID', ST['meta_red'])],
    ]
    meta = Table(meta_rows, colWidths=[22 * mm, 36 * mm])
    meta.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
    ]))

    info = Table([[bill, '', [meta]]], colWidths=[CW * 0.55, 6 * mm, CW * 0.45 - 6 * mm])
    info.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (0, 0), ZEBRA),
        ('BACKGROUND', (2, 0), (2, 0), ZEBRA),
        ('BOX', (0, 0), (0, 0), 0.6, LINE),
        ('BOX', (2, 0), (2, 0), 0.6, LINE),
        ('LINEBEFORE', (0, 0), (0, 0), 2.5, ACCENT),
        ('LEFTPADDING', (0, 0), (0, 0), 12),
        ('LEFTPADDING', (2, 0), (2, 0), 10),
        ('RIGHTPADDING', (0, 0), (0, 0), 10),
        ('RIGHTPADDING', (2, 0), (2, 0), 10),
        ('LEFTPADDING', (1, 0), (1, 0), 0),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story += [info, Spacer(1, 8 * mm)]

    # 2) Items table
    show_disc = any(Decimal(str(getattr(i, 'discount_percent', 0) or 0)) > 0 for i in items)
    if show_disc:
        widths = [10 * mm, 74 * mm, 16 * mm, 28 * mm, 20 * mm, 30 * mm]
        head = [Paragraph('#', ST['th_c']), Paragraph('DESCRIPTION', ST['th']),
                Paragraph('QTY', ST['th_c']), Paragraph('UNIT PRICE', ST['th_r']),
                Paragraph('DISC.', ST['th_r']), Paragraph('AMOUNT', ST['th_r'])]
    else:
        widths = [10 * mm, 92 * mm, 18 * mm, 28 * mm, 30 * mm]
        head = [Paragraph('#', ST['th_c']), Paragraph('DESCRIPTION', ST['th']),
                Paragraph('QTY', ST['th_c']), Paragraph('UNIT PRICE', ST['th_r']),
                Paragraph('AMOUNT', ST['th_r'])]

    data = [head]
    for it in items:
        row = [
            Paragraph(str(it.sr_no), ST['td_m']),
            Paragraph(para_text(it.product_name), ST['td_b']),
            Paragraph(str(it.quantity), ST['td_c']),
            Paragraph(fmt(it.unit_price), ST['td_r']),
        ]
        if show_disc:
            d = Decimal(str(getattr(it, 'discount_percent', 0) or 0))
            row.append(Paragraph(percent_text(d) if d > 0 else '-', ST['td_r']))
        row.append(Paragraph(fmt(it.amount), ST['td_amt']))
        data.append(row)

    items_tbl = Table(data, colWidths=widths, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), INK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, ZEBRA]),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE),
    ]))
    story += [items_tbl, Spacer(1, 6 * mm)]

    # 3) Totals + notes
    rows = [[Paragraph('Subtotal', ST['tot_l']), Paragraph(fmt(subtotal), ST['tot_v'])]]
    if discount > 0:
        label = 'Discount'
        if invoice.discount_type == 'percent':
            label += f' ({percent_text(invoice.discount_value)})'
        rows.append([Paragraph(label, ST['tot_l']), Paragraph('- ' + fmt(discount), ST['tot_red'])])
    rows.append([Paragraph('TOTAL', ST['grand_l']), Paragraph(fmt(total), ST['grand_v'])])

    last = len(rows) - 1
    totals = Table(rows, colWidths=[36 * mm, 44 * mm], hAlign='RIGHT')
    totals.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, last - 1), 0.4, LINE),
        ('BACKGROUND', (0, last), (-1, last), ACCENT),
        ('TOPPADDING', (0, last), (-1, last), 9),
        ('BOTTOMPADDING', (0, last), (-1, last), 9),
    ]))

    notes = [Paragraph('NOTES', ST['label']), Spacer(1, 3),
             Paragraph('Thank you for your business.', ST['note'])]

    bottom = Table([[notes, [totals]]], colWidths=[CW - 84 * mm, 84 * mm])
    bottom.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(KeepTogether([bottom]))

    # ── Build ────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=M, rightMargin=M, topMargin=FIRST_HEADER_H, bottomMargin=FOOTER_H,
        title=f'Invoice {invoice.invoice_number}', author=company_name,
    )
    first_frame = Frame(M, FOOTER_H, CW, H - FIRST_HEADER_H - FOOTER_H,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='first')
    later_frame = Frame(M, FOOTER_H, CW, H - LATER_HEADER_H - FOOTER_H,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='later')
    doc.addPageTemplates([
        PageTemplate(id='first', frames=[first_frame], onPage=draw_first_page),
        PageTemplate(id='later', frames=[later_frame], onPage=draw_later_page),
    ])

    footer_left = f'{company_name}  |  Generated with Invoice Shop'
    doc.build(story, canvasmaker=partial(NumberedCanvas, footer_left=footer_left))
    return buf.getvalue()
