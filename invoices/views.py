from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from .models import Invoice, InvoiceItem, UserProduct, CompanyProfile
import json, uuid
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import io


from datetime import datetime
from django.utils import timezone

# ─── Auth ───────────────────────────────────────────────
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
        else:
            messages.error(request, 'Invalid username or password!')
    return render(request, 'invoices/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ─── Product Catalog ────────────────────────────────────
@login_required
def products_view(request):
    """User's personal product catalog — add/delete products"""
    if request.method == 'POST':
        pname = request.POST.get('product_name', '').strip()
        price = request.POST.get('unit_price', '').strip()
        if pname and price:
            if UserProduct.objects.filter(user=request.user, product_name__iexact=pname).exists():
                messages.error(request, f'Product "{pname}" already exists!')
            else:
                UserProduct.objects.create(user=request.user, product_name=pname, unit_price=float(price))
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
            product.unit_price = float(price)
            product.save()
            messages.success(request, f'Product updated successfully!')
        return redirect('products')
    return render(request, 'invoices/edit_product.html', {'product': product})


@login_required
def products_json(request):
    """API endpoint: returns user's products as JSON for invoice form"""
    products = list(UserProduct.objects.filter(user=request.user).values('id', 'product_name', 'unit_price'))
    return JsonResponse({'products': products})


# ─── Dashboard ──────────────────────────────────────────
@login_required
def dashboard(request):
    invoices = Invoice.objects.filter(
        user=request.user
    ).order_by('-created_at')

    month = request.GET.get('month')

    if month:
        try:
            year, month_num = month.split('-')

            invoices = invoices.filter(
                created_at__year=int(year),
                created_at__month=int(month_num)
            )
        except ValueError:
            pass

    product_count = UserProduct.objects.filter(
        user=request.user
    ).count()

    return render(request, 'invoices/dashboard.html', {
        'invoices': invoices,
        'product_count': product_count,
        'selected_month': month,
    })

# ─── Invoices ───────────────────────────────────────────
@login_required
def create_invoice(request):
    products = UserProduct.objects.filter(user=request.user)
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name')
        customer_email = request.POST.get('customer_email', '')
        customer_address = request.POST.get('customer_address', '')
        invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"

        invoice = Invoice.objects.create(
            user=request.user,
            invoice_number=invoice_number,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_address=customer_address,
        )

        product_ids = request.POST.getlist('product_id[]')
        product_names = request.POST.getlist('product_name[]')
        quantities = request.POST.getlist('quantity[]')
        unit_prices = request.POST.getlist('unit_price[]')

        for i, (pid, pname, qty, price) in enumerate(zip(product_ids, product_names, quantities, unit_prices), 1):
            if pname.strip():
                prod_obj = None
                if pid:
                    try:
                        prod_obj = UserProduct.objects.get(pk=int(pid), user=request.user)
                    except:
                        pass
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=prod_obj,
                    sr_no=i,
                    product_name=pname.strip(),
                    quantity=int(qty),
                    unit_price=float(price),
                    amount=int(qty) * float(price),
                )

        messages.success(request, f'Invoice {invoice_number} created!')
        return redirect('invoice_detail', pk=invoice.pk)

    # Build safe JSON for JS — avoids Decimal/trailing-comma/special-char issues
    products_json_data = json.dumps({
        str(p.id): {'name': p.product_name, 'price': float(p.unit_price)}
        for p in products
    })
    return render(request, 'invoices/create_invoice.html', {
        'products': products,
        'products_json': products_json_data,
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
        invoice.customer_name = request.POST.get('customer_name')
        invoice.customer_email = request.POST.get('customer_email', '')
        invoice.customer_address = request.POST.get('customer_address', '')
        invoice.save()
        invoice.items.all().delete()

        product_ids = request.POST.getlist('product_id[]')
        product_names = request.POST.getlist('product_name[]')
        quantities = request.POST.getlist('quantity[]')
        unit_prices = request.POST.getlist('unit_price[]')

        for i, (pid, pname, qty, price) in enumerate(zip(product_ids, product_names, quantities, unit_prices), 1):
            if pname.strip():
                prod_obj = None
                if pid:
                    try:
                        prod_obj = UserProduct.objects.get(pk=int(pid), user=request.user)
                    except:
                        pass
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=prod_obj,
                    sr_no=i,
                    product_name=pname.strip(),
                    quantity=int(qty),
                    unit_price=float(price),
                    amount=int(qty) * float(price),
                )

        messages.success(request, 'Invoice updated!')
        return redirect('invoice_detail', pk=invoice.pk)

    products_json_data = json.dumps({
        str(p.id): {'name': p.product_name, 'price': float(p.unit_price)}
        for p in products
    })
    return render(request, 'invoices/edit_invoice.html', {
        'invoice': invoice,
        'products': products,
        'products_json': products_json_data,
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


@login_required
def download_pdf(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)

    # ── Company Profile ───────────────────────────────────────
    try:
        profile = request.user.company
    except:
        profile = None

    company_name    = (profile.company_name    if profile else None) or request.user.username
    company_address = (profile.address         if profile else '') or ''
    company_phone   = (profile.phone           if profile else '') or ''
    company_email   = (profile.email           if profile else '') or ''
    logo_path       = profile.company_logo.path if (profile and profile.company_logo) else None
    avatar_path     = profile.user_avatar.path  if (profile and profile.user_avatar)  else None

    # ── Colors ───────────────────────────────────────────────
    C_DARK    = colors.HexColor('#0f172a')
    C_ACCENT  = colors.HexColor('#4f46e5')
    C_ACCENT2 = colors.HexColor('#818cf8')
    C_LIGHT   = colors.HexColor('#f8fafc')
    C_BORDER  = colors.HexColor('#e2e8f0')
    C_TEXT    = colors.HexColor('#1e293b')
    C_MUTED   = colors.HexColor('#64748b')
    C_ROW_ALT = colors.HexColor('#f1f5f9')

    def rr(cv, x, y, w, h, r, fc=None, sc=None, lw=0.5):
        cv.saveState()
        if fc: cv.setFillColor(fc)
        if sc: cv.setStrokeColor(sc); cv.setLineWidth(lw)
        else:  cv.setStrokeColor(colors.transparent)
        p = cv.beginPath(); p.roundRect(x, y, w, h, r)
        cv.drawPath(p, fill=1 if fc else 0, stroke=1 if sc else 0)
        cv.restoreState()

    buf = io.BytesIO()
    W, H = A4
    M  = 18 * mm
    CW = W - 2 * M
    cv = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"Invoice {invoice.invoice_number}")

    # ════════════════════════════════════════════════
    # 1. HEADER BAND
    # ════════════════════════════════════════════════
    HEADER_H = 54 * mm
    cv.setFillColor(C_DARK)
    cv.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)

    # Decorative circles
    cv.saveState()
    cv.setFillColor(colors.HexColor('#1e1b4b'))
    cv.circle(W - 10*mm, H - 5*mm, 28*mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#312e81'))
    cv.circle(W - 2*mm, H - 22*mm, 18*mm, fill=1, stroke=0)
    cv.restoreState()

    # Accent left bar
    cv.setFillColor(C_ACCENT)
    cv.rect(0, H - HEADER_H, 4*mm, HEADER_H, fill=1, stroke=0)

    # ── Company Logo ─────────────────────────────────
    LOGO_SIZE = 14*mm
    LOGO_X    = M + 4*mm
    LOGO_Y    = H - 7*mm - LOGO_SIZE

    if logo_path:
        try:
            # Draw white rounded bg behind logo
            rr(cv, LOGO_X - 1*mm, LOGO_Y - 1*mm,
               LOGO_SIZE + 2*mm, LOGO_SIZE + 2*mm, 2*mm,
               fc=colors.white)
            cv.drawImage(logo_path, LOGO_X, LOGO_Y,
                         width=LOGO_SIZE, height=LOGO_SIZE,
                         preserveAspectRatio=True, mask='auto')
        except:
            # Fallback placeholder if image fails
            rr(cv, LOGO_X, LOGO_Y, LOGO_SIZE, LOGO_SIZE, 2*mm, fc=C_ACCENT)
            cv.setFillColor(colors.white)
            cv.setFont('Helvetica-Bold', 10)
            cv.drawCentredString(LOGO_X + LOGO_SIZE/2, LOGO_Y + 4*mm,
                                 company_name[:2].upper())
    else:
        # Initials placeholder
        rr(cv, LOGO_X, LOGO_Y, LOGO_SIZE, LOGO_SIZE, 2*mm, fc=C_ACCENT)
        cv.setFillColor(colors.white)
        cv.setFont('Helvetica-Bold', 10)
        cv.drawCentredString(LOGO_X + LOGO_SIZE/2, LOGO_Y + 4*mm,
                             company_name[:2].upper())

    # Company name & address (next to logo)
    TEXT_X = LOGO_X + LOGO_SIZE + 4*mm
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 16)
    cv.drawString(TEXT_X, H - 14*mm, company_name)
    cv.setFillColor(C_ACCENT2)
    cv.setFont('Helvetica', 8.5)
    cv.drawString(TEXT_X, H - 21*mm, company_address)

    # Phone & email (right side)
    RX = W - M - 4*mm
    cv.setFillColor(colors.HexColor('#94a3b8'))
    cv.setFont('Helvetica', 8)
    if company_phone: cv.drawRightString(RX, H - 14*mm, '📞 ' + company_phone)
    if company_email: cv.drawRightString(RX, H - 21*mm, '✉  ' + company_email)

    # INVOICE label
    cv.setFillColor(colors.white)
    cv.setFont('Helvetica-Bold', 26)
    cv.drawRightString(RX, H - 37*mm, 'INVOICE')
    cv.setFillColor(C_ACCENT2)
    cv.setFont('Helvetica', 10)
    cv.drawRightString(RX, H - 44*mm, invoice.invoice_number)

    # ── User chip (bottom-left of header) ────────────
    CHIP_Y = H - HEADER_H + 3*mm
    CHIP_X = M + 4*mm

    # Avatar
    AV_SIZE = 8*mm
    if avatar_path:
        try:
            cv.saveState()
            p = cv.beginPath()
            p.circle(CHIP_X + AV_SIZE/2, CHIP_Y + AV_SIZE/2, AV_SIZE/2)
            cv.clipPath(p, stroke=0)
            cv.drawImage(avatar_path, CHIP_X, CHIP_Y,
                         width=AV_SIZE, height=AV_SIZE,
                         preserveAspectRatio=True, mask='auto')
            cv.restoreState()
        except:
            rr(cv, CHIP_X, CHIP_Y, AV_SIZE, AV_SIZE, AV_SIZE/2,
               fc=colors.HexColor('#f59e0b'))
            cv.setFillColor(colors.white)
            cv.setFont('Helvetica-Bold', 7)
            cv.drawCentredString(CHIP_X + AV_SIZE/2, CHIP_Y + 2.5*mm,
                                 request.user.username[:1].upper())
    else:
        rr(cv, CHIP_X, CHIP_Y, AV_SIZE, AV_SIZE, AV_SIZE/2,
           fc=colors.HexColor('#f59e0b'))
        cv.setFillColor(colors.white)
        cv.setFont('Helvetica-Bold', 7)
        cv.drawCentredString(CHIP_X + AV_SIZE/2, CHIP_Y + 2.5*mm,
                             request.user.username[:1].upper())

    cv.setFillColor(colors.HexColor('#94a3b8'))
    cv.setFont('Helvetica', 7.5)
    cv.drawString(CHIP_X + AV_SIZE + 2*mm, CHIP_Y + 2.5*mm,
                  f'Prepared by: {request.user.username}')

    # ════════════════════════════════════════════════
    # 2. BILL TO + INVOICE DETAILS
    # ════════════════════════════════════════════════
    INFO_Y = H - HEADER_H - 8*mm
    INFO_H = 34*mm

    # Bill To card
    rr(cv, M, INFO_Y - INFO_H, CW*0.52, INFO_H, 3*mm,
       fc=C_LIGHT, sc=C_BORDER, lw=0.5)
    cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 7.5)
    cv.drawString(M + 5*mm, INFO_Y - 7*mm, 'BILL TO')
    cv.setFillColor(C_TEXT); cv.setFont('Helvetica-Bold', 11)
    cv.drawString(M + 5*mm, INFO_Y - 14*mm, invoice.customer_name)
    cv.setFillColor(C_MUTED); cv.setFont('Helvetica', 8.5)
    if invoice.customer_email:
        cv.drawString(M + 5*mm, INFO_Y - 20*mm, invoice.customer_email)
    if invoice.customer_address:
        cv.drawString(M + 5*mm, INFO_Y - 26*mm, (invoice.customer_address)[:55])

    # Invoice details card
    RX2 = M + CW*0.56
    RW  = CW*0.44
    rr(cv, RX2, INFO_Y - INFO_H, RW, INFO_H, 3*mm,
       fc=C_LIGHT, sc=C_BORDER, lw=0.5)
    detail_rows = [
        ('Invoice No.', invoice.invoice_number),
        ('Date',        invoice.created_at.strftime('%d %b %Y')),
        ('Prepared by', request.user.username),
    ]
    for i, (label, val) in enumerate(detail_rows):
        yy = INFO_Y - 8*mm - i * 8.5*mm
        cv.setFillColor(C_MUTED); cv.setFont('Helvetica', 8)
        cv.drawString(RX2 + 5*mm, yy, label)
        cv.setFillColor(C_TEXT); cv.setFont('Helvetica-Bold', 8.5)
        cv.drawRightString(RX2 + RW - 5*mm, yy, val)

    # ════════════════════════════════════════════════
    # 3. ITEMS TABLE
    # ════════════════════════════════════════════════
    TABLE_Y = INFO_Y - INFO_H - 8*mm
    items   = list(invoice.items.all())
    TH      = 9*mm

    rr(cv, M, TABLE_Y - TH, CW, TH, 2*mm, fc=C_DARK)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawCentredString(M + 8*mm,       TABLE_Y - 6*mm, 'SR#')
    cv.drawString(        M + 18*mm,     TABLE_Y - 6*mm, 'PRODUCT / SERVICE')
    cv.drawCentredString(M + CW - 68*mm, TABLE_Y - 6*mm, 'QTY')
    cv.drawRightString(  M + CW - 26*mm, TABLE_Y - 6*mm, 'UNIT PRICE')
    cv.drawRightString(  M + CW - 2*mm,  TABLE_Y - 6*mm, 'AMOUNT')

    ROW_H = 10*mm
    for i, item in enumerate(items):
        ry     = TABLE_Y - TH - i * ROW_H
        row_bg = colors.white if i % 2 == 0 else C_ROW_ALT
        cv.setFillColor(row_bg)
        cv.rect(M, ry - ROW_H, CW, ROW_H, fill=1, stroke=0)
        if i % 2 != 0:
            cv.setFillColor(C_ACCENT)
            cv.rect(M, ry - ROW_H, 1.5, ROW_H, fill=1, stroke=0)
        cy = ry - 6.5*mm
        cv.setFillColor(C_MUTED); cv.setFont('Helvetica-Bold', 8)
        cv.drawCentredString(M + 8*mm, cy, str(item.sr_no))
        cv.setFillColor(C_TEXT); cv.setFont('Helvetica-Bold', 9)
        name = item.product_name[:40] + ('...' if len(item.product_name) > 40 else '')
        cv.drawString(M + 18*mm, cy, name)
        cv.setFillColor(C_MUTED); cv.setFont('Helvetica', 9)
        cv.drawCentredString(M + CW - 68*mm, cy, str(item.quantity))
        cv.drawRightString(  M + CW - 26*mm, cy, f'Rs. {float(item.unit_price):,.0f}')
        cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 9)
        cv.drawRightString(  M + CW - 2*mm,  cy, f'Rs. {float(item.amount):,.0f}')

    last_y = TABLE_Y - TH - len(items) * ROW_H
    cv.setStrokeColor(C_BORDER); cv.setLineWidth(0.5)
    cv.line(M, last_y, M + CW, last_y)

    # ════════════════════════════════════════════════
    # 4. TOTALS
    # ════════════════════════════════════════════════
    total    = float(invoice.total_amount())
    TOTAL_Y  = last_y - 5*mm
    TOTAL_H  = 30*mm
    TOTAL_W  = 70*mm
    TX       = M + CW - TOTAL_W

    rr(cv, TX, TOTAL_Y - TOTAL_H, TOTAL_W, TOTAL_H, 3*mm, fc=C_DARK)
    cv.setFillColor(colors.HexColor('#94a3b8')); cv.setFont('Helvetica', 8.5)
    cv.drawString(TX + 5*mm, TOTAL_Y - 9*mm, 'Subtotal')
    cv.drawRightString(TX + TOTAL_W - 5*mm, TOTAL_Y - 9*mm, f'Rs. {total:,.0f}')
    cv.setStrokeColor(colors.HexColor('#334155')); cv.setLineWidth(0.5)
    cv.line(TX + 5*mm, TOTAL_Y - 13*mm, TX + TOTAL_W - 5*mm, TOTAL_Y - 13*mm)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 11)
    cv.drawString(TX + 5*mm, TOTAL_Y - 20*mm, 'TOTAL')
    cv.setFillColor(C_ACCENT2); cv.setFont('Helvetica-Bold', 13)
    cv.drawRightString(TX + TOTAL_W - 5*mm, TOTAL_Y - 20*mm, f'Rs. {total:,.0f}')
    rr(cv, TX + 5*mm, TOTAL_Y - 29*mm, 22*mm, 7*mm, 3*mm,
       fc=colors.HexColor('#fee2e2'))
    cv.setFillColor(colors.HexColor('#991b1b')); cv.setFont('Helvetica-Bold', 8)
    cv.drawCentredString(TX + 16*mm, TOTAL_Y - 25.5*mm, 'UNPAID')

    # ════════════════════════════════════════════════
    # 5. NOTE
    # ════════════════════════════════════════════════
    NOTE_Y = TOTAL_Y - TOTAL_H - 7*mm
    rr(cv, M, NOTE_Y - 16*mm, CW * 0.6, 16*mm, 2*mm,
       fc=colors.HexColor('#eff6ff'), sc=colors.HexColor('#bfdbfe'), lw=0.5)
    cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 7.5)
    cv.drawString(M + 5*mm, NOTE_Y - 6*mm, 'NOTE')
    cv.setFillColor(C_TEXT); cv.setFont('Helvetica', 8.5)
    cv.drawString(M + 5*mm, NOTE_Y - 12*mm,
                  'Thank You')

    # ════════════════════════════════════════════════
    # 6. FOOTER
    # ════════════════════════════════════════════════
    cv.setFillColor(C_DARK)
    cv.rect(0, 0, W, 12*mm, fill=1, stroke=0)
    cv.setFillColor(C_ACCENT)
    cv.rect(0, 0, 4*mm, 12*mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#475569')); cv.setFont('Helvetica', 7.5)
    cv.drawString(M, 4.5*mm, f'{company_name} — All rights reserved.')
    cv.setFillColor(C_ACCENT2)
    cv.drawRightString(W - M, 4.5*mm, f'Prepared by: {request.user.username}  |  Powered by Invoice Shop')

    cv.save()
    buf.seek(0)
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
    return response

@login_required
def profile_settings(request):
    """Upload company logo + user avatar"""
    profile, _ = CompanyProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username + "'s Company"}
    )
    if request.method == 'POST':
        profile.company_name = request.POST.get('company_name', profile.company_name)
        profile.phone = request.POST.get('phone', '')
        profile.email = request.POST.get('email', '')
        profile.address = request.POST.get('address', '')

        if 'company_logo' in request.FILES:
            # Delete old file
            if profile.company_logo:
                try: profile.company_logo.delete(save=False)
                except: pass
            profile.company_logo = request.FILES['company_logo']

        if 'user_avatar' in request.FILES:
            if profile.user_avatar:
                try: profile.user_avatar.delete(save=False)
                except: pass
            profile.user_avatar = request.FILES['user_avatar']

        profile.save()
        messages.success(request, 'Profile & logos updated successfully!')
        return redirect('profile_settings')

    return render(request, 'invoices/profile_settings.html', {'profile': profile})


# ─── Monthly Report ─────────────────────────────────────────
@login_required
def monthly_report(request):
    current_year  = date.today().year
    selected_year = int(request.GET.get('year', current_year))
    available_years = list(range(current_year, current_year - 5, -1))

    monthly_data = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(
            total_revenue=Sum('items__amount'),
            invoice_count=Count('id', distinct=True),
        )
        .order_by('month')
    )

    monthly_map    = {m['month'].month: m for m in monthly_data}
    months_labels  = []
    months_revenue = []
    months_count   = []
    for m in range(1, 13):
        months_labels.append(month_name[m][:3])
        if m in monthly_map:
            months_revenue.append(float(monthly_map[m]['total_revenue'] or 0))
            months_count.append(monthly_map[m]['invoice_count'])
        else:
            months_revenue.append(0)
            months_count.append(0)

    year_invoices  = Invoice.objects.filter(user=request.user, created_at__year=selected_year)
    total_revenue  = year_invoices.aggregate(total=Sum('items__amount'))['total'] or 0
    total_invoices = year_invoices.count()
    avg_invoice    = (float(total_revenue) / total_invoices) if total_invoices else 0
    best_month_idx = months_revenue.index(max(months_revenue)) if any(months_revenue) else 0
    best_month     = months_labels[best_month_idx]
    best_month_rev = months_revenue[best_month_idx]

    top_products = (
        InvoiceItem.objects
        .filter(invoice__user=request.user, invoice__created_at__year=selected_year)
        .values('product_name')
        .annotate(total_amount=Sum('amount'), total_qty=Sum('quantity'), times_sold=Count('id'))
        .order_by('-total_amount')[:5]
    )

    top_customers = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .values('customer_name')
        .annotate(total_spent=Sum('items__amount'), invoice_count=Count('id'))
        .order_by('-total_spent')[:5]
    )

    recent_invoices = Invoice.objects.filter(
        user=request.user, created_at__year=selected_year
    ).order_by('-created_at')[:5]

    today = date.today()
    this_month_rev = float(
        Invoice.objects.filter(
            user=request.user, created_at__year=today.year, created_at__month=today.month
        ).aggregate(t=Sum('items__amount'))['t'] or 0
    )
    last_month_date = (today.replace(day=1) - timedelta(days=1))
    last_month_rev  = float(
        Invoice.objects.filter(
            user=request.user,
            created_at__year=last_month_date.year,
            created_at__month=last_month_date.month
        ).aggregate(t=Sum('items__amount'))['t'] or 0
    )
    growth_pct = ((this_month_rev - last_month_rev) / last_month_rev * 100) if last_month_rev > 0 else (100 if this_month_rev > 0 else 0)

    return render(request, 'invoices/monthly_report.html', {
        'selected_year':       selected_year,
        'available_years':     available_years,
        'total_revenue':       total_revenue,
        'total_invoices':      total_invoices,
        'avg_invoice':         avg_invoice,
        'best_month':          best_month,
        'best_month_rev':      best_month_rev,
        'top_products':        top_products,
        'top_customers':       top_customers,
        'recent_invoices':     recent_invoices,
        'this_month_rev':      this_month_rev,
        'last_month_rev':      last_month_rev,
        'growth_pct':          growth_pct,
        'months_labels_json':  json.dumps(months_labels),
        'months_revenue_json': json.dumps(months_revenue),
        'months_count_json':   json.dumps(months_count),
    })


@login_required
def report_pdf(request):
    selected_year  = int(request.GET.get('year', date.today().year))
    year_invoices  = Invoice.objects.filter(user=request.user, created_at__year=selected_year)
    total_revenue  = float(year_invoices.aggregate(t=Sum('items__amount'))['t'] or 0)
    total_invoices = year_invoices.count()
    avg_invoice    = (total_revenue / total_invoices) if total_invoices else 0

    monthly_data = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(total_revenue=Sum('items__amount'), invoice_count=Count('id', distinct=True))
        .order_by('month')
    )
    monthly_map = {m['month'].month: m for m in monthly_data}

    top_products = (
        InvoiceItem.objects
        .filter(invoice__user=request.user, invoice__created_at__year=selected_year)
        .values('product_name')
        .annotate(total_amount=Sum('amount'), times_sold=Count('id'))
        .order_by('-total_amount')[:5]
    )
    top_customers = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .values('customer_name')
        .annotate(total_spent=Sum('items__amount'), invoice_count=Count('id'))
        .order_by('-total_spent')[:5]
    )

    try:    company_name = request.user.company.company_name
    except: company_name = 'Invoice Shop'

    buf = io.BytesIO()
    W, H = A4
    M = 15*mm; CW = W - 2*M
    cv = canvas.Canvas(buf, pagesize=A4)

    CD  = colors.HexColor('#0f172a')
    CA  = colors.HexColor('#4f46e5')
    CA2 = colors.HexColor('#818cf8')
    CMU = colors.HexColor('#64748b')
    CTX = colors.HexColor('#1e293b')
    CLT = colors.HexColor('#f8fafc')
    CBR = colors.HexColor('#e2e8f0')
    CGR = colors.HexColor('#22c55e')

    def rr(x, y, w, h, r, fc=None, sc=None, lw=0.5):
        cv.saveState()
        if fc: cv.setFillColor(fc)
        if sc: cv.setStrokeColor(sc); cv.setLineWidth(lw)
        else:  cv.setStrokeColor(colors.transparent)
        p = cv.beginPath(); p.roundRect(x, y, w, h, r)
        cv.drawPath(p, fill=1 if fc else 0, stroke=1 if sc else 0)
        cv.restoreState()

    # Header
    cv.setFillColor(CD); cv.rect(0, H-40*mm, W, 40*mm, fill=1, stroke=0)
    cv.setFillColor(CA); cv.rect(0, H-40*mm, 4*mm, 40*mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#1e1b4b')); cv.circle(W-15*mm, H-8*mm, 22*mm, fill=1, stroke=0)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 20)
    cv.drawString(M+4*mm, H-15*mm, 'Monthly Revenue Report')
    cv.setFillColor(CA2); cv.setFont('Helvetica', 9)
    cv.drawString(M+4*mm, H-23*mm, f'{company_name}  —  Year {selected_year}')
    cv.setFillColor(CMU); cv.setFont('Helvetica', 8)
    cv.drawString(M+4*mm, H-30*mm, f'Generated: {date.today().strftime("%d %b %Y")}')

    # Stat cards
    SY = H-40*mm-6*mm; SH = 22*mm
    stats = [('TOTAL REVENUE', f'Rs. {total_revenue:,.0f}', CA),
             ('TOTAL INVOICES', str(total_invoices), CGR),
             ('AVG INVOICE', f'Rs. {avg_invoice:,.0f}', colors.HexColor('#f59e0b')),
             ('YEAR', str(selected_year), CMU)]
    sw = (CW - 9*mm) / 4
    for i, (lbl, val, col) in enumerate(stats):
        sx = M + i*(sw+3*mm)
        rr(sx, SY-SH, sw, SH, 2*mm, fc=CLT, sc=CBR)
        cv.setFillColor(col); cv.setFont('Helvetica-Bold', 7)
        cv.drawString(sx+4*mm, SY-7*mm, lbl)
        cv.setFillColor(CTX); cv.setFont('Helvetica-Bold', 11)
        cv.drawString(sx+4*mm, SY-16*mm, val)

    # Monthly table
    TY = SY-SH-8*mm
    cv.setFillColor(CD); cv.rect(M, TY-8*mm, CW, 8*mm, fill=1, stroke=0)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawString(M+4*mm, TY-5.5*mm, 'MONTH')
    cv.drawRightString(M+CW*0.45, TY-5.5*mm, 'INVOICES')
    cv.drawRightString(M+CW*0.75, TY-5.5*mm, 'REVENUE')
    cv.drawRightString(M+CW-2*mm, TY-5.5*mm, 'BAR')

    max_rev = max((float(monthly_map[m]['total_revenue'] or 0) for m in monthly_map), default=1) or 1
    RH = 7.5*mm
    for i, mo in enumerate(range(1, 13)):
        ry  = TY-8*mm-i*RH
        rev = float(monthly_map[mo]['total_revenue'] or 0) if mo in monthly_map else 0
        cnt = monthly_map[mo]['invoice_count'] if mo in monthly_map else 0
        cv.setFillColor(CLT if i%2==0 else colors.white)
        cv.rect(M, ry-RH, CW, RH, fill=1, stroke=0)
        cy = ry-RH+2.5*mm
        cv.setFillColor(CTX); cv.setFont('Helvetica-Bold', 8)
        cv.drawString(M+4*mm, cy, month_name[mo])
        cv.setFillColor(CMU); cv.setFont('Helvetica', 8)
        cv.drawRightString(M+CW*0.45, cy, str(cnt) if cnt else '—')
        cv.setFillColor(CTX if rev>0 else CMU)
        cv.setFont('Helvetica-Bold' if rev>0 else 'Helvetica', 8)
        cv.drawRightString(M+CW*0.75, cy, f'Rs. {rev:,.0f}' if rev>0 else '—')
        BX = M+CW*0.78; BW = CW*0.18
        cv.setFillColor(CBR); cv.rect(BX, cy+0.5*mm, BW, 3*mm, fill=1, stroke=0)
        if rev > 0:
            cv.setFillColor(CA); cv.rect(BX, cy+0.5*mm, (rev/max_rev)*BW, 3*mm, fill=1, stroke=0)

    # Top products & customers
    PY  = TY-8*mm-12*RH-8*mm
    PW  = CW*0.48
    cv.setFillColor(CD); cv.rect(M, PY-8*mm, PW, 8*mm, fill=1, stroke=0)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawString(M+4*mm, PY-5.5*mm, 'TOP PRODUCTS')
    for i, p in enumerate(top_products):
        ry = PY-8*mm-i*RH; rev = float(p['total_amount'] or 0)
        cv.setFillColor(CLT if i%2==0 else colors.white)
        cv.rect(M, ry-RH, PW, RH, fill=1, stroke=0)
        cy = ry-RH+2.5*mm
        cv.setFillColor(CA); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawString(M+3*mm, cy, f'{i+1}.')
        cv.setFillColor(CTX); cv.setFont('Helvetica', 7.5)
        nm = p['product_name'][:28]+('…' if len(p['product_name'])>28 else '')
        cv.drawString(M+8*mm, cy, nm)
        cv.setFillColor(CA); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawRightString(M+PW-2*mm, cy, f'Rs. {rev:,.0f}')

    CX = M+CW*0.52; CW2 = CW*0.48
    cv.setFillColor(CD); cv.rect(CX, PY-8*mm, CW2, 8*mm, fill=1, stroke=0)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawString(CX+4*mm, PY-5.5*mm, 'TOP CUSTOMERS')
    for i, cust in enumerate(top_customers):
        ry = PY-8*mm-i*RH; rev = float(cust['total_spent'] or 0)
        cv.setFillColor(CLT if i%2==0 else colors.white)
        cv.rect(CX, ry-RH, CW2, RH, fill=1, stroke=0)
        cy = ry-RH+2.5*mm
        cv.setFillColor(CGR); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawString(CX+3*mm, cy, f'{i+1}.')
        cv.setFillColor(CTX); cv.setFont('Helvetica', 7.5)
        cn = cust['customer_name'][:26]+('…' if len(cust['customer_name'])>26 else '')
        cv.drawString(CX+8*mm, cy, cn)
        cv.setFillColor(CGR); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawRightString(CX+CW2-2*mm, cy, f'Rs. {rev:,.0f}')

    # Footer
    cv.setFillColor(CD); cv.rect(0, 0, W, 10*mm, fill=1, stroke=0)
    cv.setFillColor(CA); cv.rect(0, 0, 4*mm, 10*mm, fill=1, stroke=0)
    cv.setFillColor(CMU); cv.setFont('Helvetica', 7)
    cv.drawString(M, 3.5*mm, f'{company_name} — Revenue Report {selected_year}')
    cv.setFillColor(CA2); cv.drawRightString(W-M, 3.5*mm, 'Powered by Invoice Shop')

    cv.save(); buf.seek(0)
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="Revenue_Report_{selected_year}.pdf"'
# ─── Auth ───────────────────────────────────────────────
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
        else:
            messages.error(request, 'Invalid username or password!')
    return render(request, 'invoices/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ─── Product Catalog ────────────────────────────────────
@login_required
def products_view(request):
    """User's personal product catalog — add/delete products"""
    if request.method == 'POST':
        pname = request.POST.get('product_name', '').strip()
        price = request.POST.get('unit_price', '').strip()
        if pname and price:
            if UserProduct.objects.filter(user=request.user, product_name__iexact=pname).exists():
                messages.error(request, f'Product "{pname}" already exists!')
            else:
                UserProduct.objects.create(user=request.user, product_name=pname, unit_price=float(price))
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
            product.unit_price = float(price)
            product.save()
            messages.success(request, f'Product updated successfully!')
        return redirect('products')
    return render(request, 'invoices/edit_product.html', {'product': product})


@login_required
def products_json(request):
    """API endpoint: returns user's products as JSON for invoice form"""
    products = list(UserProduct.objects.filter(user=request.user).values('id', 'product_name', 'unit_price'))
    return JsonResponse({'products': products})




# ─── Invoices ───────────────────────────────────────────
@login_required
def create_invoice(request):
    products = UserProduct.objects.filter(user=request.user)
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name')
        customer_email = request.POST.get('customer_email', '')
        customer_address = request.POST.get('customer_address', '')
        invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"

        invoice = Invoice.objects.create(
            user=request.user,
            invoice_number=invoice_number,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_address=customer_address,
        )

        product_ids = request.POST.getlist('product_id[]')
        product_names = request.POST.getlist('product_name[]')
        quantities = request.POST.getlist('quantity[]')
        unit_prices = request.POST.getlist('unit_price[]')

        for i, (pid, pname, qty, price) in enumerate(zip(product_ids, product_names, quantities, unit_prices), 1):
            if pname.strip():
                prod_obj = None
                if pid:
                    try:
                        prod_obj = UserProduct.objects.get(pk=int(pid), user=request.user)
                    except:
                        pass
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=prod_obj,
                    sr_no=i,
                    product_name=pname.strip(),
                    quantity=int(qty),
                    unit_price=float(price),
                    amount=int(qty) * float(price),
                )

        messages.success(request, f'Invoice {invoice_number} created!')
        return redirect('invoice_detail', pk=invoice.pk)

    # Build safe JSON for JS — avoids Decimal/trailing-comma/special-char issues
    products_json_data = json.dumps({
        str(p.id): {'name': p.product_name, 'price': float(p.unit_price)}
        for p in products
    })
    return render(request, 'invoices/create_invoice.html', {
        'products': products,
        'products_json': products_json_data,
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
        invoice.customer_name = request.POST.get('customer_name')
        invoice.customer_email = request.POST.get('customer_email', '')
        invoice.customer_address = request.POST.get('customer_address', '')
        invoice.save()
        invoice.items.all().delete()

        product_ids = request.POST.getlist('product_id[]')
        product_names = request.POST.getlist('product_name[]')
        quantities = request.POST.getlist('quantity[]')
        unit_prices = request.POST.getlist('unit_price[]')

        for i, (pid, pname, qty, price) in enumerate(zip(product_ids, product_names, quantities, unit_prices), 1):
            if pname.strip():
                prod_obj = None
                if pid:
                    try:
                        prod_obj = UserProduct.objects.get(pk=int(pid), user=request.user)
                    except:
                        pass
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=prod_obj,
                    sr_no=i,
                    product_name=pname.strip(),
                    quantity=int(qty),
                    unit_price=float(price),
                    amount=int(qty) * float(price),
                )

        messages.success(request, 'Invoice updated!')
        return redirect('invoice_detail', pk=invoice.pk)

    products_json_data = json.dumps({
        str(p.id): {'name': p.product_name, 'price': float(p.unit_price)}
        for p in products
    })
    return render(request, 'invoices/edit_invoice.html', {
        'invoice': invoice,
        'products': products,
        'products_json': products_json_data,
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


# ─────────────────────────────────────────────────────────────
# Paste this function in your invoices/views.py
# Replace the existing download_pdf function completely
# ─────────────────────────────────────────────────────────────

# @login_required
# def download_pdf(request, pk):
#     invoice = get_object_or_404(Invoice, pk=pk, user=request.user)

#     # ── Get company profile ───────────────────────────────────
#     try:
#         profile = request.user.company
#     except:
#         profile = None

#     company_name    = profile.company_name    if profile else 'Invoice Shop'
#     company_address = profile.address         if profile else ''
#     company_phone   = profile.phone           if profile else ''
#     company_email   = profile.email           if profile else ''

#     # ── Colors ───────────────────────────────────────────────
#     C_DARK     = colors.HexColor('#0f172a')
#     C_ACCENT   = colors.HexColor('#4f46e5')
#     C_ACCENT2  = colors.HexColor('#818cf8')
#     C_LIGHT_BG = colors.HexColor('#f8fafc')
#     C_BORDER   = colors.HexColor('#e2e8f0')
#     C_TEXT     = colors.HexColor('#1e293b')
#     C_MUTED    = colors.HexColor('#64748b')
#     C_ROW_ALT  = colors.HexColor('#f1f5f9')

#     def rrect(cv, x, y, w, h, r, fc=None, sc=None, lw=0):
#         cv.saveState()
#         if fc: cv.setFillColor(fc)
#         if sc: cv.setStrokeColor(sc); cv.setLineWidth(lw)
#         else:  cv.setStrokeColor(colors.transparent)
#         p = cv.beginPath(); p.roundRect(x, y, w, h, r)
#         cv.drawPath(p, fill=1 if fc else 0, stroke=1 if sc else 0)
#         cv.restoreState()

#     buf = io.BytesIO()
#     W, H = A4
#     M  = 18 * mm
#     CW = W - 2 * M
#     cv = canvas.Canvas(buf, pagesize=A4)
#     cv.setTitle(f"Invoice {invoice.invoice_number}")

#     # ── 1. HEADER ────────────────────────────────────────────
#     HEADER_H = 52 * mm
#     cv.setFillColor(C_DARK)
#     cv.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)

#     # Decorative circles
#     cv.saveState()
#     cv.setFillColor(colors.HexColor('#1e1b4b'))
#     cv.circle(W - 10*mm, H - 5*mm, 28*mm, fill=1, stroke=0)
#     cv.setFillColor(colors.HexColor('#312e81'))
#     cv.circle(W - 2*mm, H - 22*mm, 18*mm, fill=1, stroke=0)
#     cv.restoreState()

#     # Accent left bar
#     cv.setFillColor(C_ACCENT)
#     cv.rect(0, H - HEADER_H, 4*mm, HEADER_H, fill=1, stroke=0)

#     # Company name
#     cv.setFillColor(colors.white)
#     cv.setFont('Helvetica-Bold', 22)
#     cv.drawString(M + 4*mm, H - 18*mm, company_name)
#     cv.setFillColor(C_ACCENT2)
#     cv.setFont('Helvetica', 9)
#     cv.drawString(M + 4*mm, H - 25*mm, company_address)

#     # Company contact
#     cv.setFillColor(colors.HexColor('#94a3b8'))
#     cv.setFont('Helvetica', 8)
#     rx = W - M - 4*mm
#     cv.drawRightString(rx, H - 17*mm, company_phone)
#     cv.drawRightString(rx, H - 24*mm, company_email)

#     # INVOICE label
#     cv.setFillColor(colors.white)
#     cv.setFont('Helvetica-Bold', 28)
#     cv.drawRightString(rx, H - 41*mm, 'INVOICE')
#     cv.setFillColor(C_ACCENT2)
#     cv.setFont('Helvetica', 10)
#     cv.drawRightString(rx, H - 48*mm, invoice.invoice_number)

#     # ── 2. INFO ROW ───────────────────────────────────────────
#     INFO_Y = H - HEADER_H - 8*mm
#     INFO_H = 34*mm

#     # Bill To card
#     rrect(cv, M, INFO_Y - INFO_H, CW*0.52, INFO_H, 3*mm,
#           fc=C_LIGHT_BG, sc=C_BORDER, lw=0.5)
#     cv.setFillColor(C_ACCENT)
#     cv.setFont('Helvetica-Bold', 7.5)
#     cv.drawString(M + 5*mm, INFO_Y - 7*mm, 'BILL TO')
#     cv.setFillColor(C_TEXT)
#     cv.setFont('Helvetica-Bold', 11)
#     cv.drawString(M + 5*mm, INFO_Y - 14*mm, invoice.customer_name)
#     cv.setFillColor(C_MUTED)
#     cv.setFont('Helvetica', 8.5)
#     cv.drawString(M + 5*mm, INFO_Y - 20*mm, invoice.customer_email or '')
#     cv.drawString(M + 5*mm, INFO_Y - 26*mm, (invoice.customer_address or '')[:55])

#     # Invoice details card
#     RX = M + CW*0.56
#     RW = CW*0.44
#     rrect(cv, RX, INFO_Y - INFO_H, RW, INFO_H, 3*mm,
#           fc=C_LIGHT_BG, sc=C_BORDER, lw=0.5)
#     detail_rows = [
#         ('Invoice No.', invoice.invoice_number),
#         ('Date',        invoice.created_at.strftime('%d %b %Y')),
#         ('Status',      'UNPAID'),
#     ]
#     for i, (label, val) in enumerate(detail_rows):
#         yy = INFO_Y - 8*mm - i * 8.5*mm
#         cv.setFillColor(C_MUTED); cv.setFont('Helvetica', 8)
#         cv.drawString(RX + 5*mm, yy, label)
#         cv.setFillColor(C_TEXT); cv.setFont('Helvetica-Bold', 8.5)
#         if label == 'Status':
#             rrect(cv, RX + RW - 27*mm, yy - 2*mm, 22*mm, 6*mm, 3*mm,
#                   fc=colors.HexColor('#dcfce7'))
#             cv.setFillColor(colors.HexColor('#166534'))
#             cv.setFont('Helvetica-Bold', 7)
#             cv.drawCentredString(RX + RW - 16*mm, yy + 0.5*mm, 'UNPAID')
#         else:
#             cv.drawRightString(RX + RW - 5*mm, yy, val)

    # ── 3. ITEMS TABLE ────────────────────────────────────────
    TABLE_Y = INFO_Y - INFO_H - 8*mm
    items   = list(invoice.items.all())
    TH      = 9*mm

    # Table header
    rrect(cv, M, TABLE_Y - TH, CW, TH, 2*mm, fc=C_DARK)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawCentredString(M + 8*mm,       TABLE_Y - 6*mm, 'SR#')
    cv.drawString(        M + 18*mm,     TABLE_Y - 6*mm, 'PRODUCT / SERVICE')
    cv.drawCentredString(M + CW - 68*mm, TABLE_Y - 6*mm, 'QTY')
    cv.drawRightString(  M + CW - 26*mm, TABLE_Y - 6*mm, 'UNIT PRICE')
    cv.drawRightString(  M + CW - 2*mm,  TABLE_Y - 6*mm, 'AMOUNT')

    ROW_H = 10*mm
    for i, item in enumerate(items):
        ry = TABLE_Y - TH - i * ROW_H
        row_bg = colors.white if i % 2 == 0 else C_ROW_ALT
        cv.setFillColor(row_bg)
        cv.rect(M, ry - ROW_H, CW, ROW_H, fill=1, stroke=0)
        if i % 2 != 0:
            cv.setFillColor(C_ACCENT)
            cv.rect(M, ry - ROW_H, 1.5, ROW_H, fill=1, stroke=0)

        cy = ry - 6.5*mm
        cv.setFillColor(C_MUTED);  cv.setFont('Helvetica-Bold', 8)
        cv.drawCentredString(M + 8*mm, cy, str(item.sr_no))
        cv.setFillColor(C_TEXT);   cv.setFont('Helvetica-Bold', 9)
        name = item.product_name[:40] + ('...' if len(item.product_name) > 40 else '')
        cv.drawString(M + 18*mm, cy, name)
        cv.setFillColor(C_MUTED);  cv.setFont('Helvetica', 9)
        cv.drawCentredString(M + CW - 68*mm, cy, str(item.quantity))
        cv.drawRightString(M + CW - 26*mm,   cy, f'Rs. {float(item.unit_price):,.0f}')
        cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 9)
        cv.drawRightString(M + CW - 2*mm,    cy, f'Rs. {float(item.amount):,.0f}')

    last_y = TABLE_Y - TH - len(items) * ROW_H
    cv.setStrokeColor(C_BORDER); cv.setLineWidth(0.5)
    cv.line(M, last_y, M + CW, last_y)

    # ── 4. TOTALS ─────────────────────────────────────────────
    total = float(invoice.total_amount())
    TOTAL_Y   = last_y - 5*mm
    TOTAL_H   = 30*mm
    TOTAL_W   = 70*mm
    TX        = M + CW - TOTAL_W

    rrect(cv, TX, TOTAL_Y - TOTAL_H, TOTAL_W, TOTAL_H, 3*mm, fc=C_DARK)
    cv.setFillColor(colors.HexColor('#94a3b8'))
    cv.setFont('Helvetica', 8.5)
    cv.drawString(TX + 5*mm, TOTAL_Y - 9*mm, 'Subtotal')
    cv.drawRightString(TX + TOTAL_W - 5*mm, TOTAL_Y - 9*mm, f'Rs. {total:,.0f}')
    cv.setStrokeColor(colors.HexColor('#334155')); cv.setLineWidth(0.5)
    cv.line(TX + 5*mm, TOTAL_Y - 13*mm, TX + TOTAL_W - 5*mm, TOTAL_Y - 13*mm)
    cv.setFillColor(colors.white); cv.setFont('Helvetica-Bold', 11)
    cv.drawString(TX + 5*mm, TOTAL_Y - 20*mm, 'TOTAL')
    cv.setFillColor(C_ACCENT2); cv.setFont('Helvetica-Bold', 13)
    cv.drawRightString(TX + TOTAL_W - 5*mm, TOTAL_Y - 20*mm, f'Rs. {total:,.0f}')
    rrect(cv, TX + 5*mm, TOTAL_Y - 29*mm, 22*mm, 7*mm, 3*mm,
          fc=colors.HexColor('#fee2e2'))
    cv.setFillColor(colors.HexColor('#991b1b'))
    cv.setFont('Helvetica-Bold', 8)
    cv.drawCentredString(TX + 16*mm, TOTAL_Y - 25.5*mm, 'UNPAID')

    # ── 5. NOTES ──────────────────────────────────────────────
    NOTE_Y = TOTAL_Y - TOTAL_H - 7*mm
    rrect(cv, M, NOTE_Y - 16*mm, CW * 0.6, 16*mm, 2*mm,
          fc=colors.HexColor('#eff6ff'),
          sc=colors.HexColor('#bfdbfe'), lw=0.5)
    cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 7.5)
    cv.drawString(M + 5*mm, NOTE_Y - 6*mm, 'NOTE')
    cv.setFillColor(C_TEXT); cv.setFont('Helvetica', 8.5)
    cv.drawString(M + 5*mm, NOTE_Y - 12*mm,
                  'Thank you for shopping with us.')

    # ── 6. FOOTER ─────────────────────────────────────────────
    cv.setFillColor(C_DARK)
    cv.rect(0, 0, W, 12*mm, fill=1, stroke=0)
    cv.setFillColor(C_ACCENT)
    cv.rect(0, 0, 4*mm, 12*mm, fill=1, stroke=0)
    cv.setFillColor(colors.HexColor('#475569'))
    cv.setFont('Helvetica', 7.5)
    cv.drawString(M, 4.5*mm, f'{company_name} — All rights reserved.')
    cv.setFillColor(C_ACCENT2)
    cv.drawRightString(W - M, 4.5*mm, 'Powered by Invoice Shop')

    cv.save()
    buf.seek(0)
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
    return response


# ═══════════════════════════════════════════════════════════════
# ADD TO: invoices/views.py
# ═══════════════════════════════════════════════════════════════
#
# 1. Add these imports at the top of views.py:
#
#    from django.db.models import Sum, Count
#    from django.db.models.functions import TruncMonth, TruncYear
#    from datetime import date
#    import json
#    from reportlab.pdfgen import canvas
#    from reportlab.lib.units import mm
#
# 2. Add this URL to invoices/urls.py:
#
#    path('reports/', views.monthly_report, name='monthly_report'),
#    path('reports/pdf/', views.report_pdf, name='report_pdf'),
#
# 3. Add "📊 Reports" link in base.html navbar:
#
#    <a href="{% url 'monthly_report' %}" ...>📊 Reports</a>
#
# ═══════════════════════════════════════════════════════════════

from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from datetime import date, timedelta
from calendar import month_name


@login_required
def monthly_report(request):
    """Full monthly revenue report page with charts data."""

    # ── Year filter ───────────────────────────────────────────
    current_year = date.today().year
    selected_year = int(request.GET.get('year', current_year))
    available_years = list(range(current_year, current_year - 5, -1))

    # ── Monthly revenue (current selected year) ───────────────
    monthly_data = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(
            total_revenue=Sum('items__amount'),
            invoice_count=Count('id', distinct=True),
        )
        .order_by('month')
    )

    # Build full 12-month array (fill 0 for missing months)
    monthly_map = {m['month'].month: m for m in monthly_data}
    months_labels = []
    months_revenue = []
    months_count = []
    for m in range(1, 13):
        months_labels.append(month_name[m][:3])
        if m in monthly_map:
            months_revenue.append(float(monthly_map[m]['total_revenue'] or 0))
            months_count.append(monthly_map[m]['invoice_count'])
        else:
            months_revenue.append(0)
            months_count.append(0)

    # ── Summary stats ─────────────────────────────────────────
    year_invoices = Invoice.objects.filter(
        user=request.user, created_at__year=selected_year
    )
    total_revenue = year_invoices.aggregate(
        total=Sum('items__amount')
    )['total'] or 0

    total_invoices = year_invoices.count()
    avg_invoice    = (float(total_revenue) / total_invoices) if total_invoices else 0
    best_month_idx = months_revenue.index(max(months_revenue)) if any(months_revenue) else 0
    best_month     = months_labels[best_month_idx]
    best_month_rev = months_revenue[best_month_idx]

    # ── Top 5 products (by revenue) ───────────────────────────
    top_products = (
        InvoiceItem.objects
        .filter(invoice__user=request.user, invoice__created_at__year=selected_year)
        .values('product_name')
        .annotate(
            total_amount=Sum('amount'),
            total_qty=Sum('quantity'),
            times_sold=Count('id'),
        )
        .order_by('-total_amount')[:5]
    )

    # ── Top 5 customers ───────────────────────────────────────
    top_customers = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .values('customer_name')
        .annotate(
            total_spent=Sum('items__amount'),
            invoice_count=Count('id'),
        )
        .order_by('-total_spent')[:5]
    )

    # ── Recent invoices (last 5) ──────────────────────────────
    recent_invoices = Invoice.objects.filter(
        user=request.user, created_at__year=selected_year
    ).order_by('-created_at')[:5]

    # ── This month vs last month ──────────────────────────────
    today = date.today()
    this_month_rev = float(
        Invoice.objects.filter(
            user=request.user,
            created_at__year=today.year,
            created_at__month=today.month
        ).aggregate(t=Sum('items__amount'))['t'] or 0
    )
    last_month_date = (today.replace(day=1) - timedelta(days=1))
    last_month_rev = float(
        Invoice.objects.filter(
            user=request.user,
            created_at__year=last_month_date.year,
            created_at__month=last_month_date.month
        ).aggregate(t=Sum('items__amount'))['t'] or 0
    )
    if last_month_rev > 0:
        growth_pct = ((this_month_rev - last_month_rev) / last_month_rev) * 100
    else:
        growth_pct = 100 if this_month_rev > 0 else 0

    context = {
        'selected_year':    selected_year,
        'available_years':  available_years,
        'total_revenue':    total_revenue,
        'total_invoices':   total_invoices,
        'avg_invoice':      avg_invoice,
        'best_month':       best_month,
        'best_month_rev':   best_month_rev,
        'top_products':     top_products,
        'top_customers':    top_customers,
        'recent_invoices':  recent_invoices,
        'this_month_rev':   this_month_rev,
        'last_month_rev':   last_month_rev,
        'growth_pct':       growth_pct,
        # JSON for JS chart
        'months_labels_json':  json.dumps(months_labels),
        'months_revenue_json': json.dumps(months_revenue),
        'months_count_json':   json.dumps(months_count),
    }
    return render(request, 'invoices/monthly_report.html', context)


@login_required
def report_pdf(request):
    """Download monthly report as PDF."""
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas

    selected_year = int(request.GET.get('year', date.today().year))

    # Same data queries as above
    year_invoices  = Invoice.objects.filter(user=request.user, created_at__year=selected_year)
    total_revenue  = float(year_invoices.aggregate(t=Sum('items__amount'))['t'] or 0)
    total_invoices = year_invoices.count()
    avg_invoice    = (total_revenue / total_invoices) if total_invoices else 0

    monthly_data = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(total_revenue=Sum('items__amount'), invoice_count=Count('id', distinct=True))
        .order_by('month')
    )
    monthly_map = {m['month'].month: m for m in monthly_data}

    top_products = (
        InvoiceItem.objects
        .filter(invoice__user=request.user, invoice__created_at__year=selected_year)
        .values('product_name')
        .annotate(total_amount=Sum('amount'), times_sold=Count('id'))
        .order_by('-total_amount')[:5]
    )
    top_customers = (
        Invoice.objects
        .filter(user=request.user, created_at__year=selected_year)
        .values('customer_name')
        .annotate(total_spent=Sum('items__amount'), invoice_count=Count('id'))
        .order_by('-total_spent')[:5]
    )

    try:
        profile = request.user.company
        company_name = profile.company_name
    except:
        company_name = 'Invoice Shop'

    # ── Build PDF ─────────────────────────────────────────────
    buf = io.BytesIO()
    W, H = A4
    M  = 15 * mm
    CW = W - 2 * M
    cv = rl_canvas.Canvas(buf, pagesize=A4)

    C_DARK   = rl_colors.HexColor('#0f172a')
    C_ACCENT = rl_colors.HexColor('#4f46e5')
    C_ACCENT2= rl_colors.HexColor('#818cf8')
    C_MUTED  = rl_colors.HexColor('#64748b')
    C_TEXT   = rl_colors.HexColor('#1e293b')
    C_LIGHT  = rl_colors.HexColor('#f8fafc')
    C_BORDER = rl_colors.HexColor('#e2e8f0')
    C_GREEN  = rl_colors.HexColor('#22c55e')

    def rrect(x, y, w, h, r, fc=None, sc=None, lw=0.5):
        cv.saveState()
        if fc: cv.setFillColor(fc)
        if sc: cv.setStrokeColor(sc); cv.setLineWidth(lw)
        else:  cv.setStrokeColor(rl_colors.transparent)
        p = cv.beginPath(); p.roundRect(x, y, w, h, r)
        cv.drawPath(p, fill=1 if fc else 0, stroke=1 if sc else 0)
        cv.restoreState()

    # ── Header ────────────────────────────────────────────────
    cv.setFillColor(C_DARK)
    cv.rect(0, H - 40*mm, W, 40*mm, fill=1, stroke=0)
    cv.setFillColor(C_ACCENT)
    cv.rect(0, H - 40*mm, 4*mm, 40*mm, fill=1, stroke=0)
    # Decorative circle
    cv.setFillColor(rl_colors.HexColor('#1e1b4b'))
    cv.circle(W - 15*mm, H - 8*mm, 22*mm, fill=1, stroke=0)

    cv.setFillColor(rl_colors.white)
    cv.setFont('Helvetica-Bold', 20)
    cv.drawString(M + 4*mm, H - 15*mm, f'Monthly Revenue Report')
    cv.setFillColor(C_ACCENT2)
    cv.setFont('Helvetica', 9)
    cv.drawString(M + 4*mm, H - 22*mm, f'{company_name}  —  Year {selected_year}')
    cv.setFillColor(rl_colors.HexColor('#475569'))
    cv.setFont('Helvetica', 8)
    cv.drawString(M + 4*mm, H - 29*mm, f'Generated: {date.today().strftime("%d %b %Y")}')

    # ── Summary Stats Row ─────────────────────────────────────
    STAT_Y = H - 40*mm - 6*mm
    STAT_H = 22*mm
    stat_items = [
        ('TOTAL REVENUE',  f'Rs. {total_revenue:,.0f}', C_ACCENT),
        ('TOTAL INVOICES', str(total_invoices),          C_GREEN),
        ('AVG INVOICE',    f'Rs. {avg_invoice:,.0f}',    rl_colors.HexColor('#f59e0b')),
        ('YEAR',           str(selected_year),           C_MUTED),
    ]
    sw = (CW - 3*3*mm) / 4
    for i, (label, val, color) in enumerate(stat_items):
        sx = M + i * (sw + 3*mm)
        rrect(sx, STAT_Y - STAT_H, sw, STAT_H, 2*mm, fc=C_LIGHT, sc=C_BORDER)
        cv.setFillColor(color); cv.setFont('Helvetica-Bold', 7)
        cv.drawString(sx + 4*mm, STAT_Y - 7*mm, label)
        cv.setFillColor(C_TEXT); cv.setFont('Helvetica-Bold', 11)
        cv.drawString(sx + 4*mm, STAT_Y - 16*mm, val)

    # ── Monthly Breakdown Table ───────────────────────────────
    TABLE_Y = STAT_Y - STAT_H - 8*mm
    cv.setFillColor(C_DARK)
    cv.rect(M, TABLE_Y - 8*mm, CW, 8*mm, fill=1, stroke=0)
    cv.setFillColor(rl_colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawString(M + 4*mm, TABLE_Y - 5.5*mm, 'MONTH')
    cv.drawRightString(M + CW*0.45, TABLE_Y - 5.5*mm, 'INVOICES')
    cv.drawRightString(M + CW*0.75, TABLE_Y - 5.5*mm, 'REVENUE')
    cv.drawRightString(M + CW - 2*mm, TABLE_Y - 5.5*mm, 'BAR')

    max_rev = max((float(monthly_map[m]['total_revenue'] or 0) for m in monthly_map), default=1) or 1
    ROW_H = 7.5*mm
    for i, mo in enumerate(range(1, 13)):
        ry = TABLE_Y - 8*mm - i * ROW_H
        bg = C_LIGHT if i % 2 == 0 else rl_colors.white
        cv.setFillColor(bg)
        cv.rect(M, ry - ROW_H, CW, ROW_H, fill=1, stroke=0)

        rev = float(monthly_map[mo]['total_revenue'] or 0) if mo in monthly_map else 0
        cnt = monthly_map[mo]['invoice_count'] if mo in monthly_map else 0
        cy  = ry - ROW_H + 2.5*mm

        cv.setFillColor(C_TEXT); cv.setFont('Helvetica-Bold', 8)
        cv.drawString(M + 4*mm, cy, month_name[mo])
        cv.setFillColor(C_MUTED); cv.setFont('Helvetica', 8)
        cv.drawRightString(M + CW*0.45, cy, str(cnt))
        cv.setFillColor(C_TEXT if rev > 0 else C_MUTED)
        cv.setFont('Helvetica-Bold' if rev > 0 else 'Helvetica', 8)
        cv.drawRightString(M + CW*0.75, cy, f'Rs. {rev:,.0f}' if rev > 0 else '—')

        # Mini bar
        BAR_X     = M + CW*0.78
        BAR_MAX_W = CW*0.18
        bar_w = (rev / max_rev) * BAR_MAX_W if rev > 0 else 0
        cv.setFillColor(C_BORDER)
        cv.rect(BAR_X, cy + 0.5*mm, BAR_MAX_W, 3*mm, fill=1, stroke=0)
        if bar_w > 0:
            cv.setFillColor(C_ACCENT)
            cv.rect(BAR_X, cy + 0.5*mm, bar_w, 3*mm, fill=1, stroke=0)

    # ── Top Products     <img src="{{ company_profile.company_logo.url }}" alt="Logo" class="brand-logo-img"> ─────────────────────────────────────────
    PROD_Y = TABLE_Y - 8*mm - 12 * ROW_H - 8*mm
    cv.setFillColor(C_DARK)
    cv.rect(M, PROD_Y - 8*mm, CW * 0.48, 8*mm, fill=1, stroke=0)
    cv.setFillColor(rl_colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawString(M + 4*mm, PROD_Y - 5.5*mm, 'TOP PRODUCTS')

    for i, p in enumerate(top_products):
        ry  = PROD_Y - 8*mm - i * ROW_H
        rev = float(p['total_amount'] or 0)
        bg  = C_LIGHT if i % 2 == 0 else rl_colors.white
        cv.setFillColor(bg)
        cv.rect(M, ry - ROW_H, CW * 0.48, ROW_H, fill=1, stroke=0)
        cy = ry - ROW_H + 2.5*mm
        cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawString(M + 3*mm, cy, f'{i+1}.')
        cv.setFillColor(C_TEXT); cv.setFont('Helvetica', 7.5)
        name = p['product_name'][:28] + ('…' if len(p['product_name']) > 28 else '')
        cv.drawString(M + 8*mm, cy, name)
        cv.setFillColor(C_ACCENT); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawRightString(M + CW*0.46, cy, f'Rs. {rev:,.0f}')

    # ── Top Customers ─────────────────────────────────────────
    CX = M + CW * 0.52
    CW2 = CW * 0.48
    cv.setFillColor(C_DARK)
    cv.rect(CX, PROD_Y - 8*mm, CW2, 8*mm, fill=1, stroke=0)
    cv.setFillColor(rl_colors.white); cv.setFont('Helvetica-Bold', 8)
    cv.drawString(CX + 4*mm, PROD_Y - 5.5*mm, 'TOP CUSTOMERS')

    for i, cust in enumerate(top_customers):
        ry  = PROD_Y - 8*mm - i * ROW_H
        rev = float(cust['total_spent'] or 0)
        bg  = C_LIGHT if i % 2 == 0 else rl_colors.white
        cv.setFillColor(bg)
        cv.rect(CX, ry - ROW_H, CW2, ROW_H, fill=1, stroke=0)
        cy = ry - ROW_H + 2.5*mm
        cv.setFillColor(C_GREEN); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawString(CX + 3*mm, cy, f'{i+1}.')
        cv.setFillColor(C_TEXT); cv.setFont('Helvetica', 7.5)
        cname = cust['customer_name'][:26] + ('…' if len(cust['customer_name']) > 26 else '')
        cv.drawString(CX + 8*mm, cy, cname)
        cv.setFillColor(C_GREEN); cv.setFont('Helvetica-Bold', 7.5)
        cv.drawRightString(CX + CW2 - 2*mm, cy, f'Rs. {rev:,.0f}')

    # ── Footer ────────────────────────────────────────────────
    cv.setFillColor(C_DARK)
    cv.rect(0, 0, W, 10*mm, fill=1, stroke=0)
    cv.setFillColor(C_ACCENT)
    cv.rect(0, 0, 4*mm, 10*mm, fill=1, stroke=0)
    cv.setFillColor(rl_colors.HexColor('#475569')); cv.setFont('Helvetica', 7)
    cv.drawString(M, 3.5*mm, f'{company_name} — Revenue Report {selected_year}')
    cv.setFillColor(C_ACCENT2)
    cv.drawRightString(W - M, 3.5*mm, 'Powered by Invoice Shop')

    cv.save()
    buf.seek(0)
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="Revenue_Report_{selected_year}.pdf"'
    return resp
# ─── Company Profile / Logo Settings ────────────────────
@login_required
def profile_settings(request):
    """Upload company logo + user avatar"""
    profile, _ = CompanyProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username + "'s Company"}
    )
    if request.method == 'POST':
        profile.company_name = request.POST.get('company_name', profile.company_name)
        profile.phone = request.POST.get('phone', '')
        profile.email = request.POST.get('email', '')
        profile.address = request.POST.get('address', '')

        if 'company_logo' in request.FILES:
            # Delete old file
            if profile.company_logo:
                try: profile.company_logo.delete(save=False)
                except: pass
            profile.company_logo = request.FILES['company_logo']

        if 'user_avatar' in request.FILES:
            if profile.user_avatar:
                try: profile.user_avatar.delete(save=False)
                except: pass
            profile.user_avatar = request.FILES['user_avatar']

        profile.save()
        messages.success(request, 'Profile & logos updated successfully!')
        return redirect('profile_settings')

    return render(request, 'invoices/profile_settings.html', {'profile': profile})



