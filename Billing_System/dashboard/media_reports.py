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

from sales.models import Sale, SaleItem, ReturnRequest, Payment
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


def generate_chart_image(company, date_start=None, date_end=None):
    """
    Generate an executive summary chart image (PNG).
    Draws modern dark-mode card with KPI badges, tender distributions, and sync status.
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

    if sales_count == 0 and gross_rev <= 0:
        return None, None, None

    payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
    cash_val = float(payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or 0)
    upi_val = float(payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or 0)
    card_val = float(payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or 0)
    credit_val = float(payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or 0)

    sync_info = SyncService.get_sync_status(company)
    sync_str = f"Synced: {sync_info['last_sync_timestamp_display']}" if sync_info['is_online'] else f"Offline (Last: {sync_info['last_sync_timestamp_display']})"

    # Render image canvas (880x480)
    width, height = 880, 480
    img = Image.new("RGB", (width, height), color=(15, 23, 42))  # Slate 900
    draw = ImageDraw.Draw(img)

    # Outer border
    draw.rectangle([(16, 16), (width - 16, height - 16)], outline=(51, 65, 85), width=2)

    # Title & Subtitle
    date_str = f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}" if start != end else start.strftime("%b %d, %Y")
    draw.text((40, 36), f"{company.company_name} — Sales & Tender Summary", fill=(248, 250, 252))
    draw.text((40, 60), f"Period: {date_str}  |  Sync Status: {sync_str}", fill=(148, 163, 184))

    # KPI Boxes
    # 1. Gross Revenue (INR)
    draw.rectangle([(40, 95), (280, 175)], fill=(30, 41, 59), outline=(51, 65, 85))
    draw.text((55, 108), "GROSS REVENUE (INR)", fill=(148, 163, 184))
    draw.text((55, 130), f"INR {gross_rev:,.2f}", fill=(52, 211, 153))

    # 2. Invoices
    draw.rectangle([(300, 95), (540, 175)], fill=(30, 41, 59), outline=(51, 65, 85))
    draw.text((315, 108), "COMPLETED INVOICES", fill=(148, 163, 184))
    draw.text((315, 130), f"{sales_count} Orders", fill=(56, 189, 248))

    # 3. Top Tender
    draw.rectangle([(560, 95), (840, 175)], fill=(30, 41, 59), outline=(51, 65, 85))
    draw.text((575, 108), "PRIMARY TENDER", fill=(148, 163, 184))
    top_tender_name = "Cash" if cash_val >= max(upi_val, card_val, credit_val) else ("UPI" if upi_val >= max(card_val, credit_val) else "Card")
    top_tender_val = max(cash_val, upi_val, card_val, credit_val)
    draw.text((575, 130), f"{top_tender_name} (INR {top_tender_val:,.0f})", fill=(251, 191, 36))

    # Bar Chart: Tenders Breakdown
    draw.text((40, 205), "TENDER DISTRIBUTION (INR)", fill=(203, 213, 225))
    draw.line([(40, 225), (840, 225)], fill=(51, 65, 85), width=1)

    tenders = [
        ("Cash", cash_val, (16, 185, 129)),       # Emerald
        ("UPI / QR", upi_val, (56, 189, 248)),    # Sky
        ("Card", card_val, (168, 85, 247)),       # Purple
        ("Credit", credit_val, (249, 115, 22)),   # Orange
    ]

    max_val = max(cash_val, upi_val, card_val, credit_val, 100.0)
    bar_y_start = 245
    bar_height = 24
    max_bar_width = 460

    for idx, (label, val, bar_color) in enumerate(tenders):
        curr_y = bar_y_start + (idx * 46)
        draw.text((40, curr_y + 4), f"{label:12}", fill=(226, 232, 240))

        bar_len = int((val / max_val) * max_bar_width) if max_val > 0 else 0
        if bar_len > 0:
            draw.rectangle([(170, curr_y), (170 + bar_len, curr_y + bar_height)], fill=bar_color)
        draw.text((180 + bar_len + 10, curr_y + 4), f"INR {val:,.2f}", fill=(248, 250, 252))

    # Footer
    draw.text((40, 445), "Retail POS Autonomous Sync & Reporting Engine • Powered by Django", fill=(100, 116, 139))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    buf.close()

    out_dir = os.path.join(settings.BASE_DIR, "media", "reports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"chart_{company.id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.png"
    file_path = os.path.join(out_dir, filename)
    with open(file_path, "wb") as f:
        f.write(png_bytes)

    return file_path, filename, png_bytes
