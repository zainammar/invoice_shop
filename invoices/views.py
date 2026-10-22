import io
import json
import uuid
from calendar import month_name
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .models import (
    CompanyProfile,
    Invoice,
    InvoiceItem,
    PaymentProof,
    UserProduct,
)


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════
def to_decimal(value, default='0'):
    """Safe Decimal conversion (never goes through float)."""
    try:
        return Decimal(str(value).strip() or default)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def to_int(value, default=1):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def apply_invoice_form(request, invoice):
    """Customer + discount fields POST se invoice par set karta hai."""
    invoice.customer_name = request.POST.get('customer_name', '').strip()
    invoice.customer_email = request.POST.get('customer_email', '')
    invoice.customer_address = request.POST.get('customer_address', '')

    dtype = request.POST.get('discount_type', Invoice.DISCOUNT_PERCENT)
    if dtype not in (Invoice.DISCOUNT_PERCENT, Invoice.DISCOUNT_FIXED):
        dtype = Invoice.DISCOUNT_PERCENT

    dvalue = max(to_decimal(request.POST.get('discount_value')), Decimal('0'))
    if dtype == Invoice.DISCOUNT_PERCENT:
        dvalue = min(dvalue, Decimal('100'))

    invoice.discount_type = dtype
    invoice.discount_value = dvalue


def save_invoice_items(request, invoice):
    """POST se invoice items create karta hai. `amount` model.save() calculate karta hai."""
    product_ids = request.POST.getlist('product_id[]')
    product_names = request.POST.getlist('product_name[]')
    quantities = request.POST.getlist('quantity[]')
    unit_prices = request.POST.getlist('unit_price[]')
    item_discounts = request.POST.getlist('discount_percent[]')  # optional

    has_item_discount = hasattr(InvoiceItem, 'discount_percent')

    rows = zip(product_ids, product_names, quantities, unit_prices)
    for i, (pid, pname, qty, price) in enumerate(rows, 1):
        if not pname.strip():
            continue

        prod_obj = None
        if pid:
            prod_obj = UserProduct.objects.filter(pk=pid, user=request.user).first()

        kwargs = dict(
            invoice=invoice,
            product=prod_obj,
            sr_no=i,
            product_name=pname.strip(),
            quantity=to_int(qty, 1),
            unit_price=to_decimal(price),
        )
        if has_item_discount:
            raw = item_discounts[i - 1] if len(item_discounts) >= i else '0'
            kwargs['discount_percent'] = min(max(to_decimal(raw), Decimal('0')), Decimal('100'))

        InvoiceItem.objects.create(**kwargs)


def products_to_json(products):
    return json.dumps({
        str(p.id): {'name': p.product_name, 'price': float(p.unit_price)}
        for p in products
    })


def draw_rounded_rect(cv, x, y, w, h, r, fc=None, sc=None, lw=0.5):
    cv.saveState()
    if fc:
        cv.setFillColor(fc)
    if sc:
        cv.setStrokeColor(sc)
        cv.setLineWidth(lw)
    else:
        cv.setStrokeColor(colors.transparent)
    p = cv.beginPath()
    p.roundRect(x, y, w, h, r)
    cv.drawPath(p, fill=1 if fc else 0, stroke=1 if sc else 0)
    cv.restoreState()


# ═══════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════
def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email', '')
        password = request.POST.get('password')
        confirm = request.POST.get('confirm_password')
        if password != confirm:
            messages.error(request, 'Passwords do not match!')
            return render(request, 'invoices/register.html')
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already taken!')
            return render(request, 'invoices/register.html')
        user = User.objects.create_user(username=username, email=email, password=password)
        login(request, user)
        messages.success(request, f'Welcome, {username}! Start by adding your products.')
        return redirect('products')
    return render(request, 'invoices/register.html')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect('dashboard')
        messages.error(request, 'Invalid username or password!')
    return render(request, 'invoices/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ═══════════════════════════════════════════════════════════
# Product Catalog
# ═══════════════════════════════════════════════════════════
@login_required
def products_view(request):
    """User's personal product catalog — add products."""
    if request.method == 'POST':
        pname = request.POST.get('product_name', '').strip()
        price = request.POST.get('unit_price', '').strip()
        if pname and price:
            if UserProduct.objects.filter(user=request.user, product_name__iexact=pname).exists():
                messages.error(request, f'Product "{pname}" already exists!')
            else:
                UserProduct.objects.create(
                    user=request.user,
                    product_name=pname,
                    unit_price=to_decimal(price),
                )
                messages.success(request, f'Product "{pname}" added successfully!')
        else:
            messages.error(request, 'Please fill both Product Name and Price.')
        return redirect('products')

    products = UserProduct.objects.filter(user=request.user)
    return render(request, 'invoices/products.html', {'products': products})


@login_required
def delete_product(request, pk):
    product = get_object_or_404(UserProduct, pk=pk, user=request.user)
    if request.method == 'POST':
        name = product.product_name
        product.delete()
        messages.success(request, f'Product "{name}" deleted.')
    return redirect('products')


@login_required
def edit_product(request, pk):
    product = get_object_or_404(UserProduct, pk=pk, user=request.user)
    if request.method == 'POST':
        pname = request.POST.get('product_name', '').strip()
        price = request.POST.get('unit_price', '').strip()
        if pname and price:
            product.product_name = pname
            product.unit_price = to_decimal(price)
            product.save()
            messages.success(request, 'Product updated successfully!')
        return redirect('products')
    return render(request, 'invoices/edit_product.html', {'product': product})


@login_required
def products_json(request):
    """API endpoint: returns user's products as JSON for invoice form."""
    products = list(
        UserProduct.objects.filter(user=request.user)
        .values('id', 'product_name', 'unit_price')
    )
    return JsonResponse({'products': products})


# ═══════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════
@login_required
def dashboard(request):
    invoices = Invoice.objects.filter(user=request.user).order_by('-created_at')

    month = request.GET.get('month')
    if month:
        try:
            year, month_num = month.split('-')
            invoices = invoices.filter(
                created_at__year=int(year),
                created_at__month=int(month_num),
            )
        except ValueError:
            pass

    product_count = UserProduct.objects.filter(user=request.user).count()

    return render(request, 'invoices/dashboard.html', {
        'invoices': invoices,
        'product_count': product_count,
        'selected_month': month,
    })


# ═══════════════════════════════════════════════════════════
# Invoices
# ═══════════════════════════════════════════════════════════
@login_required
def create_invoice(request):
    products = UserProduct.objects.filter(user=request.user)

    if request.method == 'POST':
        invoice = Invoice(
            user=request.user,
            invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
        )
        apply_invoice_form(request, invoice)
        invoice.save()
        save_invoice_items(request, invoice)

        messages.success(request, f'Invoice {invoice.invoice_number} created!')
        return redirect('invoice_detail', pk=invoice.pk)

    return render(request, 'invoices/create_invoice.html', {
        'products': products,
        'products_json': products_to_json(products),
    })


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)
    return render(request, 'invoices/invoice_detail.html', {'invoice': invoice})


@login_required
def edit_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)
    products = UserProduct.objects.filter(user=request.user)

    if request.method == 'POST':
        apply_invoice_form(request, invoice)
        invoice.save()
        invoice.items.all().delete()
        save_invoice_items(request, invoice)

        messages.success(request, 'Invoice updated!')
        return redirect('invoice_detail', pk=invoice.pk)

    return render(request, 'invoices/edit_invoice.html', {
        'invoice': invoice,
        'products': products,
        'products_json': products_to_json(products),
    })


@login_required
def delete_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)
    if request.method == 'POST':
        inv_num = invoice.invoice_number
        invoice.delete()
        messages.success(request, f'Invoice {inv_num} deleted.')
        return redirect('dashboard')
    return render(request, 'invoices/confirm_delete.html', {'invoice': invoice})


# ═══════════════════════════════════════════════════════════
# Invoice PDF
# ═══════════════════════════════════════════════════════════
@login_required
def download_pdf(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)

    # ── Company profile ──────────────────────────────────────
    profile = CompanyProfile.objects.filter(user=request.user).first()

    company_name = (profile.company_name if profile else None) or request.user.username
    company_address = (profile.address if profile else '') or ''
    company_phone = (profile.phone if profile else '') or ''
    company_email = (profile.email if profile else '') or ''
    logo_path = profile.company_logo.path if (profile and profile.company_logo) else None
    avatar_path = profile.user_avatar.path if (profile and profile.user_avatar) else None

    # ── Colors ───────────────────────────────────────────────
    C_DARK = colors.HexColor('#0f172a')
    C_ACCENT = colors.HexColor('#4f46e5')
    C_ACCENT2 = colors.HexColor('#818cf8')
    C_LIGHT = colors.HexColor('#f8fafc')
    C_BORDER = colors.HexColor('#e2e8f0')
    C_TEXT = colors.HexColor('#1e293b')
    C_MUTED = colors.HexColor('#64748b')
    C_ROW_ALT = colors.HexColor('#f1f5f9')
    C_SLATE = colors.HexColor('#94a3b8')

    buf = io.BytesIO()
    W, H = A4
    M = 18 * mm
    CW = W - 2 * M
    cv = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"Invoice {invoice.invoice_number}")

    # ════════ 1. HEADER BAND ════════
    HEADER_H = 54 * mm
    cv.setFillColor(C_DARK)
    cv.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)

    cv.saveState()
    cv.setFillColor(colors.HexColor('#1e1b4b'))
    cv.circle(W - 10 * mm, H - 5 * mm, 28 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#312e81'))
    cv.circle(W - 2 * mm, H - 22 * mm, 18 * mm, fill=1, stroke=0)
    cv.restoreState()

    cv.setFillColor(C_ACCENT)
    cv.rect(0, H - HEADER_H, 4 * mm, HEADER_H, fill=1, stroke=0)

    # Logo
    LOGO_SIZE = 14 * mm
    LOGO_X = M + 4 * mm
    LOGO_Y = H - 7 * mm - LOGO_SIZE

    def draw_logo_placeholder():
        draw_rounded_rect(cv, LOGO_X, LOGO_Y, LOGO_SIZE, LOGO_SIZE, 2 * mm, fc=C_ACCENT)
        cv.setFillColor(colors.white)
        cv.setFont('Helvetica-Bold', 10)
        cv.drawCentredString(LOGO_X + LOGO_SIZE / 2, LOGO_Y + 4 * mm, company_name[:2].upper())

    if logo_path:
        try:
            draw_rounded_rect(cv, LOGO_X - 1 * mm, LOGO_Y - 1 * mm,
                              LOGO_SIZE + 2 * mm, LOGO_SIZE + 2 * mm, 2 * mm, fc=colors.white)
            cv.drawImage(logo_path, LOGO_X, LOGO_Y, width=LOGO_SIZE, height=LOGO_SIZE,
                         preserveAspectRatio=True, mask='auto')
        except Exception:
            draw_logo_placeholder()
    else:
        draw_logo_placeholder()

    # Company name + address
    TEXT_X = LOGO_X + LOGO_SIZE + 4 * mm
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 16)
    cv.drawString(TEXT_X, H - 14 * mm, company_name)
    cv.setFillColor(C_ACCENT2)
    cv.setFont('Helvetica', 8.5)
    cv.drawString(TEXT_X, H - 21 * mm, company_address[:60])

    # Phone / email (plain text — emoji Helvetica mein render nahi hote)
    RX = W - M - 4 * mm
    cv.setFillColor(C_SLATE)
    cv.setFont('Helvetica', 8)
    if company_phone:
        cv.drawRightString(RX, H - 14 * mm, f'Tel: {company_phone}')
    if company_email:
        cv.drawRightString(RX, H - 21 * mm, f'Email: {company_email}')

    # INVOICE label
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 26)
    cv.drawRightString(RX, H - 37 * mm, 'INVOICE')
    cv.setFillColor(C_ACCENT2)
    cv.setFont('Helvetica', 10)
    cv.drawRightString(RX, H - 44 * mm, invoice.invoice_number)

    # User chip
    CHIP_Y = H - HEADER_H + 3 * mm
    CHIP_X = M + 4 * mm
    AV_SIZE = 8 * mm

    def draw_avatar_placeholder():
        draw_rounded_rect(cv, CHIP_X, CHIP_Y, AV_SIZE, AV_SIZE, AV_SIZE / 2,
                          fc=colors.HexColor('#f59e0b'))
        cv.setFillColor(colors.white)
        cv.setFont('Helvetica-Bold', 7)
        cv.drawCentredString(CHIP_X + AV_SIZE / 2, CHIP_Y + 2.5 * mm,
                             request.user.username[:1].upper())

    if avatar_path:
        try:
            cv.saveState()
            p = cv.beginPath()
            p.circle(CHIP_X + AV_SIZE / 2, CHIP_Y + AV_SIZE / 2, AV_SIZE / 2)
            cv.clipPath(p, stroke=0)
            cv.drawImage(avatar_path, CHIP_X, CHIP_Y, width=AV_SIZE, height=AV_SIZE,
                         preserveAspectRatio=True, mask='auto')
            cv.restoreState()
        except Exception:
            cv.restoreState()
            draw_avatar_placeholder()
    else:
        draw_avatar_placeholder()

    cv.setFillColor(C_SLATE)
    cv.setFont('Helvetica', 7.5)
    cv.drawString(CHIP_X + AV_SIZE + 2 * mm, CHIP_Y + 2.5 * mm,
                  f'Prepared by: {request.user.username}')

    # ════════ 2. BILL TO + INVOICE DETAILS ════════
    INFO_Y = H - HEADER_H - 8 * mm
    INFO_H = 34 * mm

    draw_rounded_rect(cv, M, INFO_Y - INFO_H, CW * 0.52, INFO_H, 3 * mm,
                      fc=C_LIGHT, sc=C_BORDER, lw=0.5)
    cv.setFillColor(C_ACCENT)
    cv.setFont('Helvetica-Bold', 7.5)
    cv.drawString(M + 5 * mm, INFO_Y - 7 * mm, 'BILL TO')
    cv.setFillColor(C_TEXT)
    cv.setFont('Helvetica-Bold', 11)
    cv.drawString(M + 5 * mm, INFO_Y - 14 * mm, invoice.customer_name)
    cv.setFillColor(C_MUTED)
    cv.setFont('Helvetica', 8.5)
    if invoice.customer_email:
        cv.drawString(M + 5 * mm, INFO_Y - 20 * mm, invoice.customer_email)
    if invoice.customer_address:
        cv.drawString(M + 5 * mm, INFO_Y - 26 * mm, invoice.customer_address[:55])

    RX2 = M + CW * 0.56
    RW = CW * 0.44
    draw_rounded_rect(cv, RX2, INFO_Y - INFO_H, RW, INFO_H, 3 * mm,
                      fc=C_LIGHT, sc=C_BORDER, lw=0.5)
    detail_rows = [
        ('Invoice No.', invoice.invoice_number),
        ('Date', timezone.localtime(invoice.created_at).strftime('%d %b %Y')),
        ('Prepared by', request.user.username),
    ]
    for i, (label, val) in enumerate(detail_rows):
        yy = INFO_Y - 8 * mm - i * 8.5 * mm
        cv.setFillColor(C_MUTED)
        cv.setFont('Helvetica', 8)
        cv.drawString(RX2 + 5 * mm, yy, label)
        cv.setFillColor(C_TEXT)
        cv.setFont('Helvetica-Bold', 8.5)
        cv.drawRightString(RX2 + RW - 5 * mm, yy, val)

    # ════════ 3. ITEMS TABLE ════════
    TABLE_Y = INFO_Y - INFO_H - 8 * mm
    items = list(invoice.items.all())
    TH = 9 * mm

    draw_rounded_rect(cv, M, TABLE_Y - TH, CW, TH, 2 * mm, fc=C_DARK)
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 8)
    cv.drawCentredString(M + 8 * mm, TABLE_Y - 6 * mm, 'SR#')
    cv.drawString(M + 18 * mm, TABLE_Y - 6 * mm, 'PRODUCT / SERVICE')
    cv.drawCentredString(M + CW - 68 * mm, TABLE_Y - 6 * mm, 'QTY')
    cv.drawRightString(M + CW - 26 * mm, TABLE_Y - 6 * mm, 'UNIT PRICE')
    cv.drawRightString(M + CW - 2 * mm, TABLE_Y - 6 * mm, 'AMOUNT')

    ROW_H = 10 * mm
    for i, item in enumerate(items):
        ry = TABLE_Y - TH - i * ROW_H
        cv.setFillColor(colors.white if i % 2 == 0 else C_ROW_ALT)
        cv.rect(M, ry - ROW_H, CW, ROW_H, fill=1, stroke=0)
        if i % 2 != 0:
            cv.setFillColor(C_ACCENT)
            cv.rect(M, ry - ROW_H, 1.5, ROW_H, fill=1, stroke=0)

        cy = ry - 6.5 * mm
        cv.setFillColor(C_MUTED)
        cv.setFont('Helvetica-Bold', 8)
        cv.drawCentredString(M + 8 * mm, cy, str(item.sr_no))

        cv.setFillColor(C_TEXT)
        cv.setFont('Helvetica-Bold', 9)
        name = item.product_name[:40] + ('...' if len(item.product_name) > 40 else '')
        cv.drawString(M + 18 * mm, cy, name)

        cv.setFillColor(C_MUTED)
        cv.setFont('Helvetica', 9)
        cv.drawCentredString(M + CW - 68 * mm, cy, str(item.quantity))
        cv.drawRightString(M + CW - 26 * mm, cy, f'Rs. {float(item.unit_price):,.0f}')

        cv.setFillColor(C_ACCENT)
        cv.setFont('Helvetica-Bold', 9)
        cv.drawRightString(M + CW - 2 * mm, cy, f'Rs. {float(item.amount):,.0f}')

    last_y = TABLE_Y - TH - len(items) * ROW_H
    cv.setStrokeColor(C_BORDER)
    cv.setLineWidth(0.5)
    cv.line(M, last_y, M + CW, last_y)

    # ════════ 4. TOTALS (with discount) ════════
    subtotal = float(invoice.subtotal())
    discount = float(invoice.discount_amount())
    total = float(invoice.total_amount())
    has_disc = discount > 0
    off = 6 * mm if has_disc else 0

    TOTAL_Y = last_y - 5 * mm
    TOTAL_H = 30 * mm + off
    TOTAL_W = 70 * mm
    TX = M + CW - TOTAL_W

    draw_rounded_rect(cv, TX, TOTAL_Y - TOTAL_H, TOTAL_W, TOTAL_H, 3 * mm, fc=C_DARK)
    cv.setFillColor(C_SLATE)
    cv.setFont('Helvetica', 8.5)
    cv.drawString(TX + 5 * mm, TOTAL_Y - 9 * mm, 'Subtotal')
    cv.drawRightString(TX + TOTAL_W - 5 * mm, TOTAL_Y - 9 * mm, f'Rs. {subtotal:,.0f}')

    if has_disc:
        label = 'Discount'
        if invoice.discount_type == Invoice.DISCOUNT_PERCENT:
            label += f' ({float(invoice.discount_value):g}%)'
        cv.drawString(TX + 5 * mm, TOTAL_Y - 15 * mm, label)
        cv.drawRightString(TX + TOTAL_W - 5 * mm, TOTAL_Y - 15 * mm, f'- Rs. {discount:,.0f}')

    cv.setStrokeColor(colors.HexColor('#334155'))
    cv.setLineWidth(0.5)
    cv.line(TX + 5 * mm, TOTAL_Y - 13 * mm - off, TX + TOTAL_W - 5 * mm, TOTAL_Y - 13 * mm - off)

    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 11)
    cv.drawString(TX + 5 * mm, TOTAL_Y - 20 * mm - off, 'TOTAL')
    cv.setFillColor(C_ACCENT2)
    cv.setFont('Helvetica-Bold', 13)
    cv.drawRightString(TX + TOTAL_W - 5 * mm, TOTAL_Y - 20 * mm - off, f'Rs. {total:,.0f}')

    draw_rounded_rect(cv, TX + 5 * mm, TOTAL_Y - 29 * mm - off, 22 * mm, 7 * mm, 3 * mm,
                      fc=colors.HexColor('#fee2e2'))
    cv.setFillColor(colors.HexColor('#991b1b'))
    cv.setFont('Helvetica-Bold', 8)
    cv.drawCentredString(TX + 16 * mm, TOTAL_Y - 25.5 * mm - off, 'UNPAID')

    # ════════ 5. NOTE ════════
    NOTE_Y = TOTAL_Y - TOTAL_H - 7 * mm
    draw_rounded_rect(cv, M, NOTE_Y - 16 * mm, CW * 0.6, 16 * mm, 2 * mm,
                      fc=colors.HexColor('#eff6ff'), sc=colors.HexColor('#bfdbfe'), lw=0.5)
    cv.setFillColor(C_ACCENT)
    cv.setFont('Helvetica-Bold', 7.5)
    cv.drawString(M + 5 * mm, NOTE_Y - 6 * mm, 'NOTE')
    cv.setFillColor(C_TEXT)
    cv.setFont('Helvetica', 8.5)
    cv.drawString(M + 5 * mm, NOTE_Y - 12 * mm, 'Thank You')

    # ════════ 6. FOOTER ════════
    cv.setFillColor(C_DARK)
    cv.rect(0, 0, W, 12 * mm, fill=1, stroke=0)
    cv.setFillColor(C_ACCENT)
    cv.rect(0, 0, 4 * mm, 12 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#475569'))
    cv.setFont('Helvetica', 7.5)
    cv.drawString(M, 4.5 * mm, f'{company_name} — All rights reserved.')
    cv.setFillColor(C_ACCENT2)
    cv.drawRightString(W - M, 4.5 * mm,
                       f'Prepared by: {request.user.username}  |  Powered by Invoice Shop')

    cv.save()
    buf.seek(0)
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
    return response


# ═══════════════════════════════════════════════════════════
# Company Profile / Logo Settings
# ═══════════════════════════════════════════════════════════
@login_required
def profile_settings(request):
    """Upload company logo + user avatar."""
    profile, _ = CompanyProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username + "'s Company"},
    )
    if request.method == 'POST':
        profile.company_name = request.POST.get('company_name', profile.company_name)
        profile.phone = request.POST.get('phone', '')
        profile.email = request.POST.get('email', '')
        profile.address = request.POST.get('address', '')

        if 'company_logo' in request.FILES:
            if profile.company_logo:
                try:
                    profile.company_logo.delete(save=False)
                except Exception:
                    pass
            profile.company_logo = request.FILES['company_logo']

        if 'user_avatar' in request.FILES:
            if profile.user_avatar:
                try:
                    profile.user_avatar.delete(save=False)
                except Exception:
                    pass
            profile.user_avatar = request.FILES['user_avatar']

        profile.save()
        messages.success(request, 'Profile & logos updated successfully!')
        return redirect('profile_settings')

    return render(request, 'invoices/profile_settings.html', {'profile': profile})


# ═══════════════════════════════════════════════════════════
# Reports  (invoice-level discount ke baad ka real revenue)
# ═══════════════════════════════════════════════════════════
def get_selected_year(request):
    try:
        return int(request.GET.get('year', date.today().year))
    except ValueError:
        return date.today().year


def month_revenue(user, year, month):
    invoices = (
        Invoice.objects
        .filter(user=user, created_at__year=year, created_at__month=month)
        .prefetch_related('items')
    )
    return float(sum((inv.total_amount() for inv in invoices), Decimal('0')))


def build_report_data(user, year):
    """Revenue discount-adjusted hai (Invoice.total_amount() se calculate)."""
    invoices = list(
        Invoice.objects
        .filter(user=user, created_at__year=year)
        .prefetch_related('items')
        .order_by('-created_at')
    )

    monthly_revenue = [Decimal('0')] * 12
    monthly_count = [0] * 12
    customers = {}

    for inv in invoices:
        idx = timezone.localtime(inv.created_at).month - 1
        total = inv.total_amount()
        monthly_revenue[idx] += total
        monthly_count[idx] += 1

        c = customers.setdefault(
            inv.customer_name,
            {'customer_name': inv.customer_name, 'total_spent': Decimal('0'), 'invoice_count': 0},
        )
        c['total_spent'] += total
        c['invoice_count'] += 1

    total_revenue = sum(monthly_revenue, Decimal('0'))
    total_invoices = len(invoices)
    avg_invoice = (float(total_revenue) / total_invoices) if total_invoices else 0

    top_customers = sorted(customers.values(), key=lambda c: c['total_spent'], reverse=True)[:5]

    top_products = (
        InvoiceItem.objects
        .filter(invoice__user=user, invoice__created_at__year=year)
        .values('product_name')
        .annotate(total_amount=Sum('amount'), total_qty=Sum('quantity'), times_sold=Count('id'))
        .order_by('-total_amount')[:5]
    )

    return {
        'invoices': invoices,
        'monthly_revenue': monthly_revenue,
        'monthly_count': monthly_count,
        'total_revenue': total_revenue,
        'total_invoices': total_invoices,
        'avg_invoice': avg_invoice,
        'top_customers': top_customers,
        'top_products': top_products,
    }


@login_required
def monthly_report(request):
    """Full monthly revenue report page with chart data."""
    current_year = date.today().year
    selected_year = get_selected_year(request)
    available_years = list(range(current_year, current_year - 5, -1))

    data = build_report_data(request.user, selected_year)

    months_labels = [month_name[m][:3] for m in range(1, 13)]
    months_revenue = [float(v) for v in data['monthly_revenue']]
    months_count = data['monthly_count']

    best_idx = months_revenue.index(max(months_revenue)) if any(months_revenue) else 0
    best_month = months_labels[best_idx]
    best_month_rev = months_revenue[best_idx]

    today = date.today()
    this_month_rev = month_revenue(request.user, today.year, today.month)
    last_month_date = today.replace(day=1) - timedelta(days=1)
    last_month_rev = month_revenue(request.user, last_month_date.year, last_month_date.month)

    if last_month_rev > 0:
        growth_pct = ((this_month_rev - last_month_rev) / last_month_rev) * 100
    else:
        growth_pct = 100 if this_month_rev > 0 else 0

    return render(request, 'invoices/monthly_report.html', {
        'selected_year': selected_year,
        'available_years': available_years,
        'total_revenue': data['total_revenue'],
        'total_invoices': data['total_invoices'],
        'avg_invoice': data['avg_invoice'],
        'best_month': best_month,
        'best_month_rev': best_month_rev,
        'top_products': data['top_products'],
        'top_customers': data['top_customers'],
        'recent_invoices': data['invoices'][:5],
        'this_month_rev': this_month_rev,
        'last_month_rev': last_month_rev,
        'growth_pct': growth_pct,
        'months_labels_json': json.dumps(months_labels),
        'months_revenue_json': json.dumps(months_revenue),
        'months_count_json': json.dumps(months_count),
    })


@login_required
def report_pdf(request):
    """Download monthly report as PDF."""
    selected_year = get_selected_year(request)
    data = build_report_data(request.user, selected_year)

    total_revenue = float(data['total_revenue'])
    total_invoices = data['total_invoices']
    avg_invoice = data['avg_invoice']
    monthly_revenue = [float(v) for v in data['monthly_revenue']]
    monthly_count = data['monthly_count']
    top_products = data['top_products']
    top_customers = data['top_customers']

    profile = CompanyProfile.objects.filter(user=request.user).first()
    company_name = (profile.company_name if profile else None) or 'Invoice Shop'

    buf = io.BytesIO()
    W, H = A4
    M = 15 * mm
    CW = W - 2 * M
    cv = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"Revenue Report {selected_year}")

    CD = colors.HexColor('#0f172a')
    CA = colors.HexColor('#4f46e5')
    CA2 = colors.HexColor('#818cf8')
    CMU = colors.HexColor('#64748b')
    CTX = colors.HexColor('#1e293b')
    CLT = colors.HexColor('#f8fafc')
    CBR = colors.HexColor('#e2e8f0')
    CGR = colors.HexColor('#22c55e')

    # ── Header ──
    cv.setFillColor(CD)
    cv.rect(0, H - 40 * mm, W, 40 * mm, fill=1, stroke=0)
    cv.setFillColor(CA)
    cv.rect(0, H - 40 * mm, 4 * mm, 40 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#1e1b4b'))
    cv.circle(W - 15 * mm, H - 8 * mm, 22 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 20)
    cv.drawString(M + 4 * mm, H - 15 * mm, 'Monthly Revenue Report')
    cv.setFillColor(CA2)
    cv.setFont('Helvetica', 9)
    cv.drawString(M + 4 * mm, H - 23 * mm, f'{company_name}  —  Year {selected_year}')
    cv.setFillColor(CMU)
    cv.setFont('Helvetica', 8)
    cv.drawString(M + 4 * mm, H - 30 * mm, f'Generated: {date.today().strftime("%d %b %Y")}')

    # ── Stat cards ──
    SY = H - 40 * mm - 6 * mm
    SH = 22 * mm
    stats = [
        ('TOTAL REVENUE', f'Rs. {total_revenue:,.0f}', CA),
        ('TOTAL INVOICES', str(total_invoices), CGR),
        ('AVG INVOICE', f'Rs. {avg_invoice:,.0f}', colors.HexColor('#f59e0b')),
        ('YEAR', str(selected_year), CMU),
    ]
    sw = (CW - 9 * mm) / 4
    for i, (lbl, val, col) in enumerate(stats):
        sx = M + i * (sw + 3 * mm)
        draw_rounded_rect(cv, sx, SY - SH, sw, SH, 2 * mm, fc=CLT, sc=CBR)
        cv.setFillColor(col)
        cv.setFont('Helvetica-Bold', 7)
        cv.drawString(sx + 4 * mm, SY - 7 * mm, lbl)
        cv.setFillColor(CTX)
        cv.setFont('Helvetica-Bold', 11)
        cv.drawString(sx + 4 * mm, SY - 16 * mm, val)

    # ── Monthly table ──
    TY = SY - SH - 8 * mm
    cv.setFillColor(CD)
    cv.rect(M, TY - 8 * mm, CW, 8 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 8)
    cv.drawString(M + 4 * mm, TY - 5.5 * mm, 'MONTH')
    cv.drawRightString(M + CW * 0.45, TY - 5.5 * mm, 'INVOICES')
    cv.drawRightString(M + CW * 0.75, TY - 5.5 * mm, 'REVENUE')
    cv.drawRightString(M + CW - 2 * mm, TY - 5.5 * mm, 'BAR')

    max_rev = max(monthly_revenue) or 1
    RH = 7.5 * mm
    for i in range(12):
        ry = TY - 8 * mm - i * RH
        rev = monthly_revenue[i]
        cnt = monthly_count[i]

        cv.setFillColor(CLT if i % 2 == 0 else colors.white)
        cv.rect(M, ry - RH, CW, RH, fill=1, stroke=0)
        cy = ry - RH + 2.5 * mm

        cv.setFillColor(CTX)
        cv.setFont('Helvetica-Bold', 8)
        cv.drawString(M + 4 * mm, cy, month_name[i + 1])
        cv.setFillColor(CMU)
        cv.setFont('Helvetica', 8)
        cv.drawRightString(M + CW * 0.45, cy, str(cnt) if cnt else '-')
        cv.setFillColor(CTX if rev > 0 else CMU)
        cv.setFont('Helvetica-Bold' if rev > 0 else 'Helvetica', 8)
        cv.drawRightString(M + CW * 0.75, cy, f'Rs. {rev:,.0f}' if rev > 0 else '-')

        BX = M + CW * 0.78
        BW = CW * 0.18
        cv.setFillColor(CBR)
        cv.rect(BX, cy + 0.5 * mm, BW, 3 * mm, fill=1, stroke=0)
        if rev > 0:
            cv.setFillColor(CA)
            cv.rect(BX, cy + 0.5 * mm, (rev / max_rev) * BW, 3 * mm, fill=1, stroke=0)

    # ── Top products ──
    PY = TY - 8 * mm - 12 * RH - 8 * mm
    PW = CW * 0.48
    cv.setFillColor(CD)
    cv.rect(M, PY - 8 * mm, PW, 8 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 8)
    cv.drawString(M + 4 * mm, PY - 5.5 * mm, 'TOP PRODUCTS')

    for i, p in enumerate(top_products):
        ry = PY - 8 * mm - i * RH
        rev = float(p['total_amount'] or 0)
        cv.setFillColor(CLT if i % 2 == 0 else colors.white)
        cv.rect(M, ry - RH, PW, RH, fill=1, stroke=0)
        cy = ry - RH + 2.5 * mm
        cv.setFillColor(CA)
        cv.setFont('Helvetica-Bold', 7.5)
        cv.drawString(M + 3 * mm, cy, f'{i + 1}.')
        cv.setFillColor(CTX)
        cv.setFont('Helvetica', 7.5)
        nm = p['product_name'][:28] + ('...' if len(p['product_name']) > 28 else '')
        cv.drawString(M + 8 * mm, cy, nm)
        cv.setFillColor(CA)
        cv.setFont('Helvetica-Bold', 7.5)
        cv.drawRightString(M + PW - 2 * mm, cy, f'Rs. {rev:,.0f}')

    # ── Top customers ──
    CX = M + CW * 0.52
    CW2 = CW * 0.48
    cv.setFillColor(CD)
    cv.rect(CX, PY - 8 * mm, CW2, 8 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 8)
    cv.drawString(CX + 4 * mm, PY - 5.5 * mm, 'TOP CUSTOMERS')

    for i, cust in enumerate(top_customers):
        ry = PY - 8 * mm - i * RH
        rev = float(cust['total_spent'] or 0)
        cv.setFillColor(CLT if i % 2 == 0 else colors.white)
        cv.rect(CX, ry - RH, CW2, RH, fill=1, stroke=0)
        cy = ry - RH + 2.5 * mm
        cv.setFillColor(CGR)
        cv.setFont('Helvetica-Bold', 7.5)
        cv.drawString(CX + 3 * mm, cy, f'{i + 1}.')
        cv.setFillColor(CTX)
        cv.setFont('Helvetica', 7.5)
        cn = cust['customer_name'][:26] + ('...' if len(cust['customer_name']) > 26 else '')
        cv.drawString(CX + 8 * mm, cy, cn)
        cv.setFillColor(CGR)
        cv.setFont('Helvetica-Bold', 7.5)
        cv.drawRightString(CX + CW2 - 2 * mm, cy, f'Rs. {rev:,.0f}')

    # ── Footer ──
    cv.setFillColor(CD)
    cv.rect(0, 0, W, 10 * mm, fill=1, stroke=0)
    cv.setFillColor(CA)
    cv.rect(0, 0, 4 * mm, 10 * mm, fill=1, stroke=0)
    cv.setFillColor(CMU)
    cv.setFont('Helvetica', 7)
    cv.drawString(M, 3.5 * mm, f'{company_name} — Revenue Report {selected_year}')
    cv.setFillColor(CA2)
    cv.drawRightString(W - M, 3.5 * mm, 'Powered by Invoice Shop')

    cv.save()
    buf.seek(0)
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="Revenue_Report_{selected_year}.pdf"'
    return resp


# ═══════════════════════════════════════════════════════════
# Payment Proof
# ═══════════════════════════════════════════════════════════
@login_required
def upload_payment_proof(request):
    if request.method == "POST":
        proof_file = request.FILES.get("proof_file")
        raw_amount = request.POST.get("amount")
        amount = to_decimal(raw_amount) if raw_amount else None

        if proof_file:
            PaymentProof.objects.create(
                user=request.user,
                proof_file=proof_file,
                amount=amount,
            )
            return redirect("upload_payment_proof")

    proofs = PaymentProof.objects.filter(user=request.user).order_by("-uploaded_at")
    return render(request, "invoices/upload_payment_proof.html", {"proofs": proofs})