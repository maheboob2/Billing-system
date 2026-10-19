import os
import io
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Count, Q

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from PIL import Image, ImageDraw, ImageFont

from sales.models import Sale, SaleItem, ReturnRequest, Payment, Customer
from purchases.models import Purchase
from inventory.models import Product
from .sync_service import SyncService


def generate_executive_pdf(company, date_start=None, date_end=None):
    """
    Generate an executive financial & operational PDF report.
    Displays store name, sync timestamp, revenue KPIs, tender distributions,
    supplier purchases, return refunds, and top selling products.
    """
    today = timezone.now().date()
    start = date_start or today
    end = date_end or today

    # Fetch authoritative data from database
    sales_qs = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=start,
        created_at__date__lte=end
    )
    sales_count = sales_qs.count()
    gross_rev = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
    tax_amt = sales_qs.aggregate(t=Sum("tax_amount"))["t"] or Decimal("0.00")
    disc_amt = sales_qs.aggregate(t=Sum("discount_amount"))["t"] or Decimal("0.00")

    payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
    cash_val = payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    upi_val = payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    card_val = payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    credit_val = payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    returns_qs = ReturnRequest.objects.filter(
        company=company,
        status__in=["APPROVED", "COMPLETED"],
        updated_at__date__gte=start,
        updated_at__date__lte=end
    )
    refund_val = returns_qs.aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")
    returns_count = returns_qs.count()
    net_rev = gross_rev - refund_val

    purchases_qs = Purchase.objects.filter(
        company=company,
        purchase_date__gte=start,
        purchase_date__lte=end
    )
    purchases_count = purchases_qs.count()

    top_items = list(SaleItem.objects.filter(
        sale__company=company,
        sale__in=sales_qs
    ).values("product_name", "product_code").annotate(
        qty=Sum("quantity"),
        rev=Sum("total")
    ).order_by("-qty")[:5])

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"Last Synced: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"⚠️ Store Offline (Last synced: {sync_info['last_sync_timestamp_display']})"

    # Create PDF in-memory buffer
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "ReportMeta",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12,
    )
    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=10,
        spaceAfter=6,
    )

    elements = []

    # Header & Meta
    date_str = f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}" if start != end else start.strftime("%b %d, %Y")
    elements.append(Paragraph(f"<b>{company.company_name}</b> — Executive Report", title_style))
    elements.append(Paragraph(f"Reporting Period: <b>{date_str}</b> &nbsp;|&nbsp; <i>{sync_str}</i>", meta_style))
    elements.append(Spacer(1, 10))

    # KPI Table
    elements.append(Paragraph("Financial Summary", section_style))
    kpi_data = [
        ["Metric", "Value", "Metric", "Value"],
        ["Gross Revenue", f"Rs. {gross_rev:,.2f}", "Net Revenue", f"Rs. {net_rev:,.2f}"],
        ["Total Invoices", str(sales_count), "Refunds Paid", f"Rs. {refund_val:,.2f} ({returns_count} items)"],
        ["GST Tax Collected", f"Rs. {tax_amt:,.2f}", "Purchases Orders", f"{purchases_count} POs"],
    ]
    t_kpi = Table(kpi_data, colWidths=[130, 140, 130, 140])
    t_kpi.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
    ]))
    elements.append(t_kpi)
    elements.append(Spacer(1, 14))

    # Tender Breakdown Table
    elements.append(Paragraph("Tender / Payment Breakdown", section_style))
    total_tender = cash_val + upi_val + card_val + credit_val
    tender_data = [
        ["Payment Method", "Amount", "Share of Total"],
        ["Cash", f"Rs. {cash_val:,.2f}", f"{(cash_val/total_tender*100 if total_tender>0 else 0):.1f}%"],
        ["UPI / QR", f"Rs. {upi_val:,.2f}", f"{(upi_val/total_tender*100 if total_tender>0 else 0):.1f}%"],
        ["Debit / Credit Card", f"Rs. {card_val:,.2f}", f"{(card_val/total_tender*100 if total_tender>0 else 0):.1f}%"],
        ["Customer Credit", f"Rs. {credit_val:,.2f}", f"{(credit_val/total_tender*100 if total_tender>0 else 0):.1f}%"],
    ]
    t_tender = Table(tender_data, colWidths=[200, 170, 170])
    t_tender.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]))
    elements.append(t_tender)
    elements.append(Spacer(1, 14))

    # Top Products
    elements.append(Paragraph("Top Selling Products", section_style))
    prod_data = [["#", "Product Name", "SKU / Code", "Units Sold", "Total Revenue"]]
    if top_items:
        for idx, item in enumerate(top_items, 1):
            prod_data.append([
                str(idx),
                item["product_name"] or "Product",
                item["product_code"] or "—",
                str(item["qty"]),
                f"Rs. {item['rev']:,.2f}",
            ])
    else:
        prod_data.append(["—", "No completed sales in this period", "—", "0", "Rs. 0.00"])

    t_prod = Table(prod_data, colWidths=[30, 210, 100, 100, 100])
    t_prod.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]))
    elements.append(t_prod)

    doc.build(elements)
    pdf_bytes = buf.getvalue()
    buf.close()

    # Save to media/reports directory
    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"report_{company.id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.pdf"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(pdf_bytes)

    return file_path, filename, pdf_bytes


def _get_font(size=14, bold=False):
    """Safely loads TrueType fonts with fallbacks across Windows/Linux."""
    font_names = [
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Helvetica-Bold.ttf" if bold else "Helvetica.ttf",
    ]
    for fn in font_names:
        try:
            return ImageFont.truetype(fn, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _format_inr(val):
    """Formats numeric value into Indian Rupee style currency string."""
    try:
        dec = Decimal(str(val))
        return f"Rs. {dec:,.2f}"
    except Exception:
        return f"Rs. {val}"


def generate_sales_chart(company, date_start=None, date_end=None):
    """
    High-Resolution (1600x1000) Sales & Revenue Performance Chart.
    Metrics: Gross Invoiced Sales, Net Revenue (after refunds), Completed Orders,
    Average Order Value (AOV), GST Tax Collected, Discounts Given, and Tender Share Bars.
    """
    today = timezone.now().date()
    start = date_start or today
    end = date_end or today

    sales_qs = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=start,
        created_at__date__lte=end
    )
    sales_count = sales_qs.count()
    gross_rev = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
    tax_amt = sales_qs.aggregate(t=Sum("tax_amount"))["t"] or Decimal("0.00")
    disc_amt = sales_qs.aggregate(t=Sum("discount_amount"))["t"] or Decimal("0.00")

    returns_qs = ReturnRequest.objects.filter(
        company=company,
        status__in=["APPROVED", "COMPLETED"],
        updated_at__date__gte=start,
        updated_at__date__lte=end
    )
    refund_val = returns_qs.aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")
    net_rev = gross_rev - refund_val
    aov = (gross_rev / sales_count) if sales_count > 0 else Decimal("0.00")

    payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
    cash_val = float(payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or 0)
    upi_val = float(payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or 0)
    card_val = float(payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or 0)
    credit_val = float(payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or 0)
    total_tender = cash_val + upi_val + card_val + credit_val

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"🟢 Live Sync: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"⚠️ Store Offline (Last sync: {sync_info['last_sync_timestamp_display']})"

    width, height = 1600, 1000
    img = Image.new("RGB", (width, height), color=(9, 13, 22))  # #090d16
    draw = ImageDraw.Draw(img)

    f_title = _get_font(34, bold=True)
    f_sub = _get_font(20, bold=False)
    f_kpi_lbl = _get_font(18, bold=False)
    f_kpi_val = _get_font(30, bold=True)
    f_sec_hd = _get_font(22, bold=True)
    f_bar_lbl = _get_font(20, bold=True)
    f_bar_val = _get_font(20, bold=False)
    f_footer = _get_font(16, bold=False)

    # Outer border
    draw.rectangle([(24, 24), (width - 24, height - 24)], outline=(31, 41, 61), width=2)

    # Header
    date_str = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}" if start != end else start.strftime("%d %b %Y")
    draw.text((60, 50), f"{company.company_name} — Sales & Revenue Executive Report", fill=(248, 250, 252), font=f_title)
    draw.text((60, 95), f"Reporting Period: {date_str}   |   {sync_str}", fill=(148, 163, 184), font=f_sub)

    # 4 Top KPI Cards
    kpis = [
        ("GROSS SALES", _format_inr(gross_rev), (52, 211, 153), (30, 41, 59)),
        ("NET REVENUE", _format_inr(net_rev), (56, 189, 248), (30, 41, 59)),
        ("COMPLETED INVOICES", f"{sales_count} Orders", (251, 191, 36), (30, 41, 59)),
        ("AVG ORDER VALUE (AOV)", _format_inr(aov), (168, 85, 247), (30, 41, 59)),
    ]

    card_w = 340
    card_h = 130
    card_y = 150
    gap = 26
    start_x = 60

    for idx, (lbl, val, col_val, bg_col) in enumerate(kpis):
        cx = start_x + (idx * (card_w + gap))
        draw.rectangle([(cx, card_y), (cx + card_w, card_y + card_h)], fill=bg_col, outline=(51, 65, 85), width=2)
        draw.text((cx + 20, card_y + 20), lbl, fill=(148, 163, 184), font=f_kpi_lbl)
        draw.text((cx + 20, card_y + 60), val, fill=col_val, font=f_kpi_val)

    # Secondary KPI Strip: Taxes, Discounts, Refunds
    draw.rectangle([(60, 310), (1540, 375)], fill=(15, 23, 42), outline=(51, 65, 85), width=1)
    draw.text((80, 330), f"GST Tax Collected: {_format_inr(tax_amt)}", fill=(226, 232, 240), font=f_sec_hd)
    draw.text((600, 330), f"Discounts Applied: {_format_inr(disc_amt)}", fill=(226, 232, 240), font=f_sec_hd)
    draw.text((1120, 330), f"Approved Refunds: {_format_inr(refund_val)}", fill=(248, 113, 113), font=f_sec_hd)

    # Main Visual: Tender / Payment Collections Breakdown
    draw.text((60, 415), "COLLECTIONS & TENDER DISTRIBUTION", fill=(248, 250, 252), font=f_sec_hd)
    draw.line([(60, 450), (1540, 450)], fill=(51, 65, 85), width=2)

    tenders = [
        ("Cash Payment", cash_val, (16, 185, 129)),       # Emerald
        ("UPI / QR Code", upi_val, (56, 189, 248)),       # Sky
        ("Card Swipe / POS", card_val, (168, 85, 247)),   # Purple
        ("Store Credit", credit_val, (249, 115, 22)),     # Orange
    ]

    max_t_val = max(cash_val, upi_val, card_val, credit_val, 1.0)
    bar_y_base = 480
    bar_gap = 90
    bar_h = 42
    bar_max_w = 760

    for idx, (t_lbl, t_val, t_color) in enumerate(tenders):
        curr_y = bar_y_base + (idx * bar_gap)
        draw.text((60, curr_y + 8), f"{t_lbl:18}", fill=(226, 232, 240), font=f_bar_lbl)

        bar_len = int((t_val / max_t_val) * bar_max_w) if max_t_val > 0 else 0
        pct = (t_val / total_tender * 100) if total_tender > 0 else 0

        # Bar background track
        draw.rectangle([(320, curr_y), (320 + bar_max_w, curr_y + bar_h)], fill=(22, 30, 46), outline=(35, 48, 71))
        # Active filled bar
        if bar_len > 0:
            draw.rectangle([(320, curr_y), (320 + bar_len, curr_y + bar_h)], fill=t_color)

        draw.text((320 + bar_max_w + 25, curr_y + 8), f"{_format_inr(t_val)} ({pct:.1f}%)", fill=(248, 250, 252), font=f_bar_val)

    # Accounting Definitions & Footer
    draw.line([(60, 880), (1540, 880)], fill=(35, 48, 71), width=1)
    draw.text((60, 905), "Financial Definitions: Gross Sales = Invoiced Orders | Net Revenue = Gross Sales - Refunds | AOV = Gross Sales / Orders", fill=(100, 116, 139), font=f_footer)
    draw.text((60, 935), f"Autonomous Cloud Sync & Telegram Report Dispatcher • Generated {timezone.now().strftime('%d-%b-%Y %H:%M:%S')}", fill=(71, 85, 105), font=f_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    png_bytes = buf.getvalue()
    buf.close()

    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"sales_chart_{company.id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.png"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(png_bytes)

    return file_path, filename, png_bytes


def generate_returns_chart(company, date_start=None, date_end=None):
    """
    High-Resolution (1600x1000) Customer Returns & Refunds Analytics Chart.
    Metrics: Total Requests, Approved Returns, Pending Approvals, Rejected Requests,
    Approved Refund Total, and Restocked vs Damaged inventory quantities.
    """
    today = timezone.now().date()
    start = date_start or today
    end = date_end or today

    returns_all = ReturnRequest.objects.filter(
        company=company,
        created_at__date__gte=start,
        created_at__date__lte=end
    )
    total_reqs = returns_all.count()
    approved_reqs = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).count()
    pending_reqs = returns_all.filter(status="PENDING").count()
    rejected_reqs = returns_all.filter(status="REJECTED").count()

    approved_refund_total = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")
    pending_refund_total = returns_all.filter(status="PENDING").aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")

    # Restock quantities
    from sales.models import ReturnItem
    return_items = ReturnItem.objects.filter(return_request__in=returns_all)
    restocked_qty = float(return_items.filter(restock=True).aggregate(t=Sum("quantity"))["t"] or 0)
    quarantine_qty = float(return_items.filter(restock=False).aggregate(t=Sum("quantity"))["t"] or 0)
    total_qty = restocked_qty + quarantine_qty

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"🟢 Live Sync: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"⚠️ Store Offline"

    width, height = 1600, 1000
    img = Image.new("RGB", (width, height), color=(15, 23, 42))  # Slate 900
    draw = ImageDraw.Draw(img)

    f_title = _get_font(34, bold=True)
    f_sub = _get_font(20, bold=False)
    f_kpi_lbl = _get_font(18, bold=False)
    f_kpi_val = _get_font(30, bold=True)
    f_sec_hd = _get_font(22, bold=True)
    f_bar_lbl = _get_font(20, bold=True)
    f_bar_val = _get_font(20, bold=False)
    f_footer = _get_font(16, bold=False)

    draw.rectangle([(24, 24), (width - 24, height - 24)], outline=(51, 65, 85), width=2)

    date_str = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}" if start != end else start.strftime("%d %b %Y")
    draw.text((60, 50), f"{company.company_name} — Returns & Refund Analytics", fill=(248, 250, 252), font=f_title)
    draw.text((60, 95), f"Reporting Period: {date_str}   |   {sync_str}", fill=(148, 163, 184), font=f_sub)

    kpis = [
        ("TOTAL RETURN REQUESTS", f"{total_reqs} Requests", (226, 232, 240), (30, 41, 59)),
        ("APPROVED REFUNDS", _format_inr(approved_refund_total), (52, 211, 153), (30, 41, 59)),
        ("PENDING APPROVAL", f"{pending_reqs} ({_format_inr(pending_refund_total)})", (251, 191, 36), (30, 41, 59)),
        ("REJECTED RETURNS", f"{rejected_reqs} Requests", (248, 113, 113), (30, 41, 59)),
    ]

    card_w = 340
    card_h = 130
    card_y = 150
    gap = 26
    start_x = 60

    for idx, (lbl, val, col_val, bg_col) in enumerate(kpis):
        cx = start_x + (idx * (card_w + gap))
        draw.rectangle([(cx, card_y), (cx + card_w, card_y + card_h)], fill=bg_col, outline=(51, 65, 85), width=2)
        draw.text((cx + 20, card_y + 20), lbl, fill=(148, 163, 184), font=f_kpi_lbl)
        draw.text((cx + 20, card_y + 60), val, fill=col_val, font=f_kpi_val)

    # Status Breakdown Bars
    draw.text((60, 330), "RETURN WORKFLOW STATUS BREAKDOWN", fill=(248, 250, 252), font=f_sec_hd)
    draw.line([(60, 365), (1540, 365)], fill=(51, 65, 85), width=2)

    statuses = [
        ("Approved & Completed", approved_reqs, (16, 185, 129)),
        ("Pending Manager Action", pending_reqs, (245, 158, 11)),
        ("Rejected by Manager", rejected_reqs, (239, 68, 68)),
    ]

    max_s_val = max(total_reqs, 1)
    bar_y_base = 400
    bar_gap = 80
    bar_h = 38
    bar_max_w = 760

    for idx, (s_lbl, s_val, s_color) in enumerate(statuses):
        curr_y = bar_y_base + (idx * bar_gap)
        draw.text((60, curr_y + 6), f"{s_lbl:22}", fill=(226, 232, 240), font=f_bar_lbl)
        bar_len = int((s_val / max_s_val) * bar_max_w) if max_s_val > 0 else 0
        pct = (s_val / total_reqs * 100) if total_reqs > 0 else 0

        draw.rectangle([(360, curr_y), (360 + bar_max_w, curr_y + bar_h)], fill=(22, 30, 46), outline=(35, 48, 71))
        if bar_len > 0:
            draw.rectangle([(360, curr_y), (360 + bar_len, curr_y + bar_h)], fill=s_color)
        draw.text((360 + bar_max_w + 25, curr_y + 6), f"{s_val} requests ({pct:.1f}%)", fill=(248, 250, 252), font=f_bar_val)

    # Restock / Quarantine Breakdown
    draw.text((60, 670), "INVENTORY RESTOCK VS QUARANTINE / DAMAGED UNITS", fill=(248, 250, 252), font=f_sec_hd)
    draw.line([(60, 705), (1540, 705)], fill=(51, 65, 85), width=2)

    inv_bars = [
        ("Restocked to Inventory", restocked_qty, (56, 189, 248)),
        ("Damaged / Quarantined", quarantine_qty, (244, 63, 94)),
    ]
    max_q_val = max(total_qty, 1.0)
    for idx, (q_lbl, q_val, q_color) in enumerate(inv_bars):
        curr_y = 735 + (idx * 75)
        draw.text((60, curr_y + 6), f"{q_lbl:22}", fill=(226, 232, 240), font=f_bar_lbl)
        bar_len = int((q_val / max_q_val) * bar_max_w) if max_q_val > 0 else 0
        pct = (q_val / total_qty * 100) if total_qty > 0 else 0

        draw.rectangle([(360, curr_y), (360 + bar_max_w, curr_y + bar_h)], fill=(22, 30, 46), outline=(35, 48, 71))
        if bar_len > 0:
            draw.rectangle([(360, curr_y), (360 + bar_len, curr_y + bar_h)], fill=q_color)
        draw.text((360 + bar_max_w + 25, curr_y + 6), f"{q_val:.0f} units ({pct:.1f}%)", fill=(248, 250, 252), font=f_bar_val)

    draw.line([(60, 900), (1540, 900)], fill=(35, 48, 71), width=1)
    draw.text((60, 930), f"Customer Returns Governance • Strict Tenant Isolation • Generated {timezone.now().strftime('%d-%b-%Y %H:%M:%S')}", fill=(100, 116, 139), font=f_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    png_bytes = buf.getvalue()
    buf.close()

    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"returns_chart_{company.id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.png"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(png_bytes)

    return file_path, filename, png_bytes


def generate_payments_chart(company, date_start=None, date_end=None):
    """
    High-Resolution (1600x1000) Payment Collections & Tender Breakdown Chart.
    Metrics: Cash, UPI/QR, Card Swipes, Customer Store Credit, Invoiced vs Collected reconciliation.
    """
    today = timezone.now().date()
    start = date_start or today
    end = date_end or today

    sales_qs = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=start,
        created_at__date__lte=end
    )
    gross_invoiced = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")

    payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
    cash_val = float(payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or 0)
    upi_val = float(payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or 0)
    card_val = float(payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or 0)
    credit_val = float(payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or 0)
    total_collected = cash_val + upi_val + card_val

    # Customer accounts receivable balance
    total_credit_debt = Customer.objects.filter(company=company).aggregate(t=Sum("credit_balance"))["t"] or Decimal("0.00")

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"🟢 Live Sync: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"⚠️ Store Offline"

    width, height = 1600, 1000
    img = Image.new("RGB", (width, height), color=(10, 15, 29))  # Dark Slate
    draw = ImageDraw.Draw(img)

    f_title = _get_font(34, bold=True)
    f_sub = _get_font(20, bold=False)
    f_kpi_lbl = _get_font(18, bold=False)
    f_kpi_val = _get_font(30, bold=True)
    f_sec_hd = _get_font(22, bold=True)
    f_bar_lbl = _get_font(20, bold=True)
    f_bar_val = _get_font(20, bold=False)
    f_footer = _get_font(16, bold=False)

    draw.rectangle([(24, 24), (width - 24, height - 24)], outline=(31, 41, 61), width=2)

    date_str = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}" if start != end else start.strftime("%d %b %Y")
    draw.text((60, 50), f"{company.company_name} — Payment Collections & Tenders", fill=(248, 250, 252), font=f_title)
    draw.text((60, 95), f"Period: {date_str}   |   {sync_str}", fill=(148, 163, 184), font=f_sub)

    kpis = [
        ("TOTAL COLLECTED", _format_inr(total_collected), (52, 211, 153), (30, 41, 59)),
        ("CASH SETTLED", _format_inr(cash_val), (16, 185, 129), (30, 41, 59)),
        ("UPI / QR SETTLED", _format_inr(upi_val), (56, 189, 248), (30, 41, 59)),
        ("CARD POS SETTLED", _format_inr(card_val), (168, 85, 247), (30, 41, 59)),
    ]

    card_w = 340
    card_h = 130
    card_y = 150
    gap = 26
    start_x = 60

    for idx, (lbl, val, col_val, bg_col) in enumerate(kpis):
        cx = start_x + (idx * (card_w + gap))
        draw.rectangle([(cx, card_y), (cx + card_w, card_y + card_h)], fill=bg_col, outline=(51, 65, 85), width=2)
        draw.text((cx + 20, card_y + 20), lbl, fill=(148, 163, 184), font=f_kpi_lbl)
        draw.text((cx + 20, card_y + 60), val, fill=col_val, font=f_kpi_val)

    # Invoiced vs Collected comparison banner
    draw.rectangle([(60, 310), (1540, 375)], fill=(15, 23, 42), outline=(51, 65, 85), width=1)
    draw.text((80, 330), f"Gross Invoiced Sales: {_format_inr(gross_invoiced)}", fill=(226, 232, 240), font=f_sec_hd)
    draw.text((640, 330), f"Credit Sale (Uncollected): {_format_inr(credit_val)}", fill=(251, 146, 60), font=f_sec_hd)
    draw.text((1140, 330), f"Total Customer Debt: {_format_inr(total_credit_debt)}", fill=(248, 113, 113), font=f_sec_hd)

    # Detailed Tender Breakdown Horizontal Bars
    draw.text((60, 420), "TENDER SHARE & LIQUIDITY DISTRIBUTION", fill=(248, 250, 252), font=f_sec_hd)
    draw.line([(60, 455), (1540, 455)], fill=(51, 65, 85), width=2)

    tenders = [
        ("Cash Money", cash_val, (16, 185, 129)),
        ("UPI / Instant QR", upi_val, (56, 189, 248)),
        ("Card Payment", card_val, (168, 85, 247)),
        ("Store Credit Due", credit_val, (249, 115, 22)),
    ]

    all_tender_sum = (cash_val + upi_val + card_val + credit_val) or 1.0
    max_val = max(cash_val, upi_val, card_val, credit_val, 1.0)
    bar_y_base = 490
    bar_gap = 90
    bar_h = 42
    bar_max_w = 760

    for idx, (t_lbl, t_val, t_color) in enumerate(tenders):
        curr_y = bar_y_base + (idx * bar_gap)
        draw.text((60, curr_y + 8), f"{t_lbl:18}", fill=(226, 232, 240), font=f_bar_lbl)
        bar_len = int((t_val / max_val) * bar_max_w) if max_val > 0 else 0
        pct = (t_val / all_tender_sum * 100)

        draw.rectangle([(320, curr_y), (320 + bar_max_w, curr_y + bar_h)], fill=(22, 30, 46), outline=(35, 48, 71))
        if bar_len > 0:
            draw.rectangle([(320, curr_y), (320 + bar_len, curr_y + bar_h)], fill=t_color)
        draw.text((320 + bar_max_w + 25, curr_y + 8), f"{_format_inr(t_val)} ({pct:.1f}%)", fill=(248, 250, 252), font=f_bar_val)

    draw.line([(60, 890), (1540, 890)], fill=(35, 48, 71), width=1)
    draw.text((60, 920), f"Payment Gateway & Cash Audit • Strict Tenant Ledger Isolation • Generated {timezone.now().strftime('%d-%b-%Y %H:%M:%S')}", fill=(100, 116, 139), font=f_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    png_bytes = buf.getvalue()
    buf.close()

    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"payments_chart_{company.id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.png"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(png_bytes)

    return file_path, filename, png_bytes


def generate_inventory_chart(company, date_start=None, date_end=None):
    """
    High-Resolution (1600x1000) Stock & Inventory Health Chart.
    Metrics: Active Catalog SKUs, Total Units in Stock, Low Stock SKUs, Out of Stock SKUs, Stock Valuation.
    """
    prods = Product.objects.filter(company=company)
    total_skus = prods.count()
    total_units = float(prods.aggregate(t=Sum("current_stock"))["t"] or 0)
    low_stock_prods = prods.filter(status=True, current_stock__gt=0, current_stock__lte=5)
    low_stock_count = low_stock_prods.count()
    out_of_stock_prods = prods.filter(current_stock__lte=0)
    out_of_stock_count = out_of_stock_prods.count()
    in_stock_count = total_skus - low_stock_count - out_of_stock_count

    # Valuation: sum(current_stock * purchase_price or selling_price)
    stock_valuation = sum(
        (p.current_stock * (p.purchase_price if p.purchase_price and p.purchase_price > 0 else p.selling_price) for p in prods if p.current_stock > 0),
        Decimal("0.00")
    )

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"🟢 Live Sync: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"⚠️ Store Offline"

    width, height = 1600, 1000
    img = Image.new("RGB", (width, height), color=(15, 23, 42))  # Slate 900
    draw = ImageDraw.Draw(img)

    f_title = _get_font(34, bold=True)
    f_sub = _get_font(20, bold=False)
    f_kpi_lbl = _get_font(18, bold=False)
    f_kpi_val = _get_font(30, bold=True)
    f_sec_hd = _get_font(22, bold=True)
    f_bar_lbl = _get_font(20, bold=True)
    f_bar_val = _get_font(20, bold=False)
    f_footer = _get_font(16, bold=False)

    draw.rectangle([(24, 24), (width - 24, height - 24)], outline=(51, 65, 85), width=2)

    draw.text((60, 50), f"{company.company_name} — Inventory Health & Stock Summary", fill=(248, 250, 252), font=f_title)
    draw.text((60, 95), f"Catalog Status: Active   |   {sync_str}", fill=(148, 163, 184), font=f_sub)

    kpis = [
        ("CATALOG PRODUCTS", f"{total_skus} SKUs", (226, 232, 240), (30, 41, 59)),
        ("TOTAL STOCK ON HAND", f"{total_units:,.0f} Units", (56, 189, 248), (30, 41, 59)),
        ("LOW STOCK ALERTS", f"{low_stock_count} SKUs (≤5)", (251, 191, 36), (30, 41, 59)),
        ("STOCK ASSET VALUE", _format_inr(stock_valuation), (52, 211, 153), (30, 41, 59)),
    ]

    card_w = 340
    card_h = 130
    card_y = 150
    gap = 26
    start_x = 60

    for idx, (lbl, val, col_val, bg_col) in enumerate(kpis):
        cx = start_x + (idx * (card_w + gap))
        draw.rectangle([(cx, card_y), (cx + card_w, card_y + card_h)], fill=bg_col, outline=(51, 65, 85), width=2)
        draw.text((cx + 20, card_y + 20), lbl, fill=(148, 163, 184), font=f_kpi_lbl)
        draw.text((cx + 20, card_y + 60), val, fill=col_val, font=f_kpi_val)

    # Health Bars
    draw.text((60, 330), "INVENTORY STOCK HEALTH COMPOSITION", fill=(248, 250, 252), font=f_sec_hd)
    draw.line([(60, 365), (1540, 365)], fill=(51, 65, 85), width=2)

    bars = [
        ("Optimal In Stock (>5 units)", in_stock_count, (16, 185, 129)),
        ("Low Stock Alert (1-5 units)", low_stock_count, (245, 158, 11)),
        ("Out of Stock (0 units)", out_of_stock_count, (239, 68, 68)),
    ]

    max_val = max(total_skus, 1)
    bar_y_base = 405
    bar_gap = 85
    bar_h = 40
    bar_max_w = 760

    for idx, (b_lbl, b_val, b_col) in enumerate(bars):
        curr_y = bar_y_base + (idx * bar_gap)
        draw.text((60, curr_y + 6), f"{b_lbl:28}", fill=(226, 232, 240), font=f_bar_lbl)
        bar_len = int((b_val / max_val) * bar_max_w) if max_val > 0 else 0
        pct = (b_val / total_skus * 100) if total_skus > 0 else 0

        draw.rectangle([(420, curr_y), (420 + bar_max_w, curr_y + bar_h)], fill=(22, 30, 46), outline=(35, 48, 71))
        if bar_len > 0:
            draw.rectangle([(420, curr_y), (420 + bar_len, curr_y + bar_h)], fill=b_col)
        draw.text((420 + bar_max_w + 25, curr_y + 6), f"{b_val} SKUs ({pct:.1f}%)", fill=(248, 250, 252), font=f_bar_val)

    # Low Stock List Table preview
    draw.text((60, 690), "ITEMS REQUIRING IMMEDIATE REORDER", fill=(248, 250, 252), font=f_sec_hd)
    draw.line([(60, 725), (1540, 725)], fill=(51, 65, 85), width=2)

    if low_stock_prods.exists() or out_of_stock_prods.exists():
        alerts = list(out_of_stock_prods[:3]) + list(low_stock_prods[:3])
        for a_idx, p in enumerate(alerts[:4]):
            ay = 745 + (a_idx * 35)
            status_tag = "OUT OF STOCK" if p.current_stock <= 0 else f"LOW ({p.current_stock} {p.unit or 'pcs'})"
            tag_color = (248, 113, 113) if p.current_stock <= 0 else (251, 191, 36)
            draw.text((60, ay), f"• {p.name} ({p.product_code})", fill=(226, 232, 240), font=f_bar_val)
            draw.text((900, ay), status_tag, fill=tag_color, font=f_bar_lbl)
            draw.text((1200, ay), f"Price: {_format_inr(p.selling_price)}", fill=(148, 163, 184), font=f_bar_val)
    else:
        draw.text((60, 750), "🟢 All catalog items are currently above safety stock thresholds.", fill=(52, 211, 153), font=f_bar_val)

    draw.line([(60, 900), (1540, 900)], fill=(35, 48, 71), width=1)
    draw.text((60, 930), f"Inventory Auditing System • Real-Time Stock Movements • Generated {timezone.now().strftime('%d-%b-%Y %H:%M:%S')}", fill=(100, 116, 139), font=f_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    png_bytes = buf.getvalue()
    buf.close()

    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"inventory_chart_{company.id}_{timezone.now().strftime('%Y%m%d')}.png"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(png_bytes)

    return file_path, filename, png_bytes


def generate_all_chart(company, date_start=None, date_end=None):
    """
    High-Resolution (1600x1100) Multi-Panel Executive Master Dashboard.
    Integrates 4 quadrants: (1) Sales Performance, (2) Payment Collections,
    (3) Returns & Refunds, and (4) Stock & Inventory Valuation.
    """
    today = timezone.now().date()
    start = date_start or today
    end = date_end or today

    sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=start, created_at__date__lte=end)
    sales_count = sales_qs.count()
    gross_rev = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
    tax_amt = sales_qs.aggregate(t=Sum("tax_amount"))["t"] or Decimal("0.00")

    returns_qs = ReturnRequest.objects.filter(company=company, status__in=["APPROVED", "COMPLETED"], updated_at__date__gte=start, updated_at__date__lte=end)
    refund_val = returns_qs.aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")
    net_rev = gross_rev - refund_val

    payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
    cash_val = float(payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or 0)
    upi_val = float(payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or 0)
    card_val = float(payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or 0)
    credit_val = float(payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or 0)

    prods = Product.objects.filter(company=company)
    total_skus = prods.count()
    low_stock = prods.filter(status=True, current_stock__gt=0, current_stock__lte=5).count()
    out_stock = prods.filter(current_stock__lte=0).count()

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"🟢 Live Sync: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"⚠️ Store Offline"

    width, height = 1600, 1100
    img = Image.new("RGB", (width, height), color=(9, 13, 22))
    draw = ImageDraw.Draw(img)

    f_title = _get_font(34, bold=True)
    f_sub = _get_font(20, bold=False)
    f_sec_hd = _get_font(22, bold=True)
    f_bar_lbl = _get_font(18, bold=True)
    f_bar_val = _get_font(18, bold=False)
    f_kpi_val = _get_font(26, bold=True)
    f_kpi_lbl = _get_font(16, bold=False)
    f_footer = _get_font(15, bold=False)

    draw.rectangle([(24, 24), (width - 24, height - 24)], outline=(31, 41, 61), width=2)

    date_str = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}" if start != end else start.strftime("%d %b %Y")
    draw.text((60, 45), f"{company.company_name} — Master Executive Dashboard (360°)", fill=(248, 250, 252), font=f_title)
    draw.text((60, 88), f"Period: {date_str}   |   {sync_str}", fill=(148, 163, 184), font=f_sub)

    # 4 Quadrant Layout
    # Quad 1: Top Left - Sales & Revenue (x: 60..780, y: 135..550)
    draw.rectangle([(60, 135), (780, 560)], fill=(15, 23, 42), outline=(51, 65, 85), width=2)
    draw.text((80, 155), "1. SALES & REVENUE", fill=(52, 211, 153), font=f_sec_hd)
    draw.line([(80, 185), (760, 185)], fill=(51, 65, 85), width=1)
    draw.text((80, 205), "Gross Sales:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 200), _format_inr(gross_rev), fill=(248, 250, 252), font=f_kpi_val)
    draw.text((80, 265), "Net Revenue:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 260), _format_inr(net_rev), fill=(56, 189, 248), font=f_kpi_val)
    draw.text((80, 325), "Completed Orders:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 320), f"{sales_count} Invoices", fill=(251, 191, 36), font=f_kpi_val)
    draw.text((80, 385), "GST Tax Collected:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 380), _format_inr(tax_amt), fill=(226, 232, 240), font=f_kpi_val)
    draw.text((80, 445), "Refunds Paid:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 440), _format_inr(refund_val), fill=(248, 113, 113), font=f_kpi_val)

    # Quad 2: Top Right - Payment Collections (x: 820..1540, y: 135..560)
    draw.rectangle([(820, 135), (1540, 560)], fill=(15, 23, 42), outline=(51, 65, 85), width=2)
    draw.text((840, 155), "2. PAYMENT COLLECTIONS", fill=(56, 189, 248), font=f_sec_hd)
    draw.line([(840, 185), (1520, 185)], fill=(51, 65, 85), width=1)

    tenders_all = [
        ("Cash Payment", cash_val, (16, 185, 129)),
        ("UPI / QR Code", upi_val, (56, 189, 248)),
        ("Card POS", card_val, (168, 85, 247)),
        ("Store Credit", credit_val, (249, 115, 22)),
    ]
    max_t = max(cash_val, upi_val, card_val, credit_val, 1.0)
    for idx, (t_l, t_v, t_c) in enumerate(tenders_all):
        ty = 210 + (idx * 75)
        draw.text((840, ty), f"{t_l:14}", fill=(226, 232, 240), font=f_bar_lbl)
        b_len = int((t_v / max_t) * 260) if max_t > 0 else 0
        draw.rectangle([(1040, ty), (1300, ty + 28)], fill=(22, 30, 46))
        if b_len > 0:
            draw.rectangle([(1040, ty), (1040 + b_len, ty + 28)], fill=t_c)
        draw.text((1315, ty), _format_inr(t_v), fill=(248, 250, 252), font=f_bar_val)

    # Quad 3: Bottom Left - Returns & Approvals (x: 60..780, y: 585..1010)
    draw.rectangle([(60, 585), (780, 1010)], fill=(15, 23, 42), outline=(51, 65, 85), width=2)
    draw.text((80, 605), "3. CUSTOMER RETURNS & AUDIT", fill=(251, 191, 36), font=f_sec_hd)
    draw.line([(80, 635), (760, 635)], fill=(51, 65, 85), width=1)

    ret_count = returns_qs.count()
    draw.text((80, 660), "Approved Returns:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 655), f"{ret_count} Requests", fill=(52, 211, 153), font=f_kpi_val)
    draw.text((80, 725), "Total Refund Paid:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 720), _format_inr(refund_val), fill=(248, 113, 113), font=f_kpi_val)
    draw.text((80, 790), "Policy:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 790), "Manager Override Enforced", fill=(226, 232, 240), font=f_bar_val)
    draw.text((80, 855), "Stock Safeguard:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((320, 855), "Auto Restock on Approval", fill=(56, 189, 248), font=f_bar_val)

    # Quad 4: Bottom Right - Stock & Inventory (x: 820..1540, y: 585..1010)
    draw.rectangle([(820, 585), (1540, 1010)], fill=(15, 23, 42), outline=(51, 65, 85), width=2)
    draw.text((840, 605), "4. INVENTORY HEALTH & CATALOG", fill=(168, 85, 247), font=f_sec_hd)
    draw.line([(840, 635), (1520, 635)], fill=(51, 65, 85), width=1)

    draw.text((840, 660), "Catalog SKUs:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((1080, 655), f"{total_skus} Active Items", fill=(248, 250, 252), font=f_kpi_val)
    draw.text((840, 725), "Low Stock Alerts:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((1080, 720), f"{low_stock} SKUs (≤5)", fill=(251, 191, 36), font=f_kpi_val)
    draw.text((840, 790), "Out of Stock:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((1080, 785), f"{out_stock} SKUs", fill=(248, 113, 113), font=f_kpi_val)
    draw.text((840, 855), "Stock Valuation:", fill=(148, 163, 184), font=f_kpi_lbl)
    draw.text((1080, 850), "Asset Protected", fill=(52, 211, 153), font=f_kpi_val)

    # Footer
    draw.line([(60, 1030), (1540, 1030)], fill=(35, 48, 71), width=1)
    draw.text((60, 1050), f"Master Executive Intelligence • Retail POS Autonomous Architecture • Generated {timezone.now().strftime('%d-%b-%Y %H:%M:%S')}", fill=(100, 116, 139), font=f_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    png_bytes = buf.getvalue()
    buf.close()

    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"all_charts_{company.id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.png"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(png_bytes)

    return file_path, filename, png_bytes


def generate_chart_image(company, date_start=None, date_end=None):
    """
    Backwards-compatible alias routing to generate_sales_chart.
    """
    return generate_sales_chart(company, date_start, date_end)
