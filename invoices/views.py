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
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .forms import PaymentForm
from .invoice_pdf import build_invoice_pdf
from .models import (
    CompanyProfile,
    Invoice,
    InvoiceItem,
    Payment,
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
    invoice.customer_email = request.POST.get('customer_email', '').strip()
    invoice.customer_address = request.POST.get('customer_address', '').strip()

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

    rows = zip(product_ids, product_names, quantities, unit_prices)
    sr = 1  # sirf valid rows ko number milta hai (gap nahi aayega)
    for idx, (pid, pname, qty, price) in enumerate(rows):
        if not pname.strip():
            continue

        prod_obj = None
        if pid:
            prod_obj = UserProduct.objects.filter(pk=pid, user=request.user).first()

        raw_disc = item_discounts[idx] if idx < len(item_discounts) else '0'
        disc = min(max(to_decimal(raw_disc), Decimal('0')), Decimal('100'))

        InvoiceItem.objects.create(
            invoice=invoice,
            product=prod_obj,
            sr_no=sr,
            product_name=pname.strip(),
            quantity=to_int(qty, 1),
            unit_price=to_decimal(price),
            discount_percent=disc,
        )
        sr += 1


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
        username = (request.POST.get('username') or '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password') or ''
        confirm = request.POST.get('confirm_password') or ''

        if not username or not password:
            messages.error(request, 'Username and password are required!')
            return render(request, 'invoices/register.html')
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
    invoices = (
        Invoice.objects
        .filter(user=request.user)
        .prefetch_related('items', 'payments')
        .order_by('-created_at')
    )

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
    context = {
        'products': products,
        'products_json': products_to_json(products),
    }

    if request.method == 'POST':
        invoice = Invoice(
            user=request.user,
            invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
        )
        apply_invoice_form(request, invoice)

        if not invoice.customer_name:
            messages.error(request, 'Customer name is required.')
            return render(request, 'invoices/create_invoice.html', context)

        with transaction.atomic():
            invoice.save()
            save_invoice_items(request, invoice)

        messages.success(request, f'Invoice {invoice.invoice_number} created!')
        return redirect('invoice_detail', pk=invoice.pk)

    return render(request, 'invoices/create_invoice.html', context)


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.prefetch_related('items', 'payments'),
        pk=pk,
        user=request.user,
    )
    return render(request, 'invoices/invoice_detail.html', {'invoice': invoice})


@login_required
def edit_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)
    products = UserProduct.objects.filter(user=request.user)
    context = {
        'invoice': invoice,
        'products': products,
        'products_json': products_to_json(products),
    }

    if request.method == 'POST':
        apply_invoice_form(request, invoice)

        if not invoice.customer_name:
            messages.error(request, 'Customer name is required.')
            return render(request, 'invoices/edit_invoice.html', context)

        # Atomic: beech me error aaye to purane items gum nahi honge
        with transaction.atomic():
            invoice.save()
            invoice.items.all().delete()
            save_invoice_items(request, invoice)

        # Edit ke baad total payments se kam ho gaya to warning
        invoice = Invoice.objects.prefetch_related('items', 'payments').get(pk=invoice.pk)
        if invoice.amount_paid() > invoice.grand_total():
            messages.warning(
                request,
                'Invoice total ab received payments se kam hai. Payments check kar lein.',
            )

        messages.success(request, 'Invoice updated!')
        return redirect('invoice_detail', pk=invoice.pk)

    return render(request, 'invoices/edit_invoice.html', context)


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
# Payments (partial / full, balance tracking)
# ═══════════════════════════════════════════════════════════
@login_required
def add_payment(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.prefetch_related('items', 'payments'),
        pk=pk,
        user=request.user,
    )

    if invoice.balance_due() <= 0:
        messages.info(request, 'Ye invoice already fully paid hai.')
        return redirect('invoice_detail', pk=invoice.pk)

    if request.method == 'POST':
        form = PaymentForm(request.POST, invoice=invoice)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            payment.save()
            messages.success(request, f'Payment Rs. {payment.amount:,.0f} record ho gayi.')
            return redirect('invoice_detail', pk=invoice.pk)
    else:
        # Default amount = poora balance (full payment ke liye one-click)
        form = PaymentForm(initial={'amount': invoice.balance_due()}, invoice=invoice)

    return render(request, 'invoices/add_payment.html', {'form': form, 'invoice': invoice})


@login_required
def delete_payment(request, pk):
    payment = get_object_or_404(Payment, pk=pk, invoice__user=request.user)
    invoice_pk = payment.invoice_id
    if request.method == 'POST':
        payment.delete()
        messages.success(request, 'Payment delete ho gayi.')
    return redirect('invoice_detail', pk=invoice_pk)


# ═══════════════════════════════════════════════════════════
# Invoice PDF
# ═══════════════════════════════════════════════════════════
@login_required
def download_pdf(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.prefetch_related('items', 'payments'),
        pk=pk,
        user=request.user,
    )
    pdf_bytes = build_invoice_pdf(invoice, request.user)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
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
        .prefetch_related('items', 'payments')
        .order_by('-created_at')
    )

    monthly_revenue = [Decimal('0')] * 12
    monthly_count = [0] * 12
    customers = {}
    total_outstanding = Decimal('0')

    for inv in invoices:
        idx = timezone.localtime(inv.created_at).month - 1
        total = inv.total_amount()
        monthly_revenue[idx] += total
        monthly_count[idx] += 1
        total_outstanding += inv.balance_due()

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
        'total_outstanding': total_outstanding,
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
        'total_outstanding': data['total_outstanding'],
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
        ('OUTSTANDING', f'Rs. {float(data["total_outstanding"]):,.0f}', colors.HexColor('#ef4444')),
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
        invoice_id = request.POST.get("invoice_id")
        proof_image = request.FILES.get("proof_image")
        payment_method = request.POST.get("payment_method", "").strip()
        transaction_id = request.POST.get("transaction_id", "").strip()

        # Sirf logged-in user ke apne invoices allowed hain
        invoice = get_object_or_404(Invoice, pk=invoice_id, user=request.user)

        PaymentProof.objects.create(
            invoice=invoice,
            payment_method=payment_method,
            transaction_id=transaction_id,
            proof_image=proof_image,
        )

        messages.success(request, "Payment proof uploaded successfully.")
        return redirect("upload_payment_proof")

    invoices = Invoice.objects.filter(user=request.user).order_by("-created_at")
    proofs = (
        PaymentProof.objects
        .filter(invoice__user=request.user)
        .select_related("invoice")
        .order_by("-uploaded_at")
    )

    return render(request, "invoices/upload_payment_proof.html", {
        "invoices": invoices,
        "proofs": proofs,
    })


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.prefetch_related('items', 'payments'),
        pk=pk,
        user=request.user,
    )
    company_profile = CompanyProfile.objects.filter(user=invoice.user).first()
    return render(request, 'invoices/invoice_detail.html', {
        'invoice': invoice,
        'company_profile': company_profile,
    })