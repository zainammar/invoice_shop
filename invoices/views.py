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
import io


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
    invoices = Invoice.objects.filter(user=request.user).order_by('-created_at')
    product_count = UserProduct.objects.filter(user=request.user).count()
    return render(request, 'invoices/dashboard.html', {
        'invoices': invoices,
        'product_count': product_count,
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
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('title', fontSize=26, fontName='Helvetica-Bold',
                                  textColor=colors.HexColor('#1a1a2e'), alignment=TA_CENTER, spaceAfter=4)
    sub_style = ParagraphStyle('sub', fontSize=10, fontName='Helvetica',
                                textColor=colors.HexColor('#6c757d'), alignment=TA_CENTER, spaceAfter=16)

    story.append(Paragraph("INVOICE", title_style))
    story.append(Paragraph("Invoice Shop — Professional Billing", sub_style))

    meta_data = [
        [Paragraph(f"<b>Invoice #:</b> {invoice.invoice_number}", styles['Normal']),
         Paragraph(f"<b>Date:</b> {invoice.created_at.strftime('%d %b %Y')}", styles['Normal'])],
        [Paragraph(f"<b>Bill To:</b> {invoice.customer_name}", styles['Normal']),
         Paragraph(f"<b>Email:</b> {invoice.customer_email or '—'}", styles['Normal'])],
    ]
    if invoice.customer_address:
        meta_data.append([Paragraph(f"<b>Address:</b> {invoice.customer_address}", styles['Normal']), ''])

    meta_table = Table(meta_data, colWidths=[270, 230])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f4ff')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.HexColor('#f8f9fa'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 18))

    header_style = ParagraphStyle('th', fontSize=10, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER)
    cell_center = ParagraphStyle('cc', fontSize=9, alignment=TA_CENTER)
    cell_left = ParagraphStyle('cl', fontSize=9, alignment=TA_LEFT)
    cell_right = ParagraphStyle('cr', fontSize=9, alignment=TA_RIGHT)

    table_data = [[
        Paragraph("SR#", header_style),
        Paragraph("Product Name", header_style),
        Paragraph("Quantity", header_style),
        Paragraph("Unit Price", header_style),
        Paragraph("Amount", header_style),
    ]]
    for item in invoice.items.all():
        table_data.append([
            Paragraph(str(item.sr_no), cell_center),
            Paragraph(item.product_name, cell_left),
            Paragraph(str(item.quantity), cell_center),
            Paragraph(f"Rs. {item.unit_price:,.2f}", cell_right),
            Paragraph(f"Rs. {item.amount:,.2f}", cell_right),
        ])

    items_table = Table(table_data, colWidths=[40, 215, 65, 90, 90], repeatRows=1)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 12))

    total = invoice.total_amount()
    total_data = [['', '', '',
        Paragraph('<b>TOTAL:</b>', ParagraphStyle('tr', alignment=TA_RIGHT, fontSize=11)),
        Paragraph(f'<b>Rs. {total:,.2f}</b>', ParagraphStyle('tr2', alignment=TA_RIGHT, fontSize=11,
                                                              textColor=colors.HexColor('#1a1a2e')))]]
    total_table = Table(total_data, colWidths=[40, 215, 65, 90, 90])
    total_table.setStyle(TableStyle([
        ('BACKGROUND', (3, 0), (-1, 0), colors.HexColor('#e8ecff')),
        ('GRID', (3, 0), (-1, 0), 0.5, colors.HexColor('#adb5bd')),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (3, 0), (-1, -1), 8),
    ]))
    story.append(total_table)
    story.append(Spacer(1, 30))
    story.append(Paragraph("Thank you for your business! — Invoice Shop",
                            ParagraphStyle('foot', fontSize=8, textColor=colors.HexColor('#6c757d'), alignment=TA_CENTER)))
    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
    return response


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
