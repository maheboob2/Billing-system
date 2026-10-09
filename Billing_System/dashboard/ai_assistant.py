import re
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Count, Q

from sales.models import Sale, SaleItem, ReturnRequest, Payment
from inventory.models import Product


def generate_sales_insights(company):
    """
    Generate data-backed AI sales insights for the store.
    Read-only, tenant-isolated analysis of revenue, velocity, and payment distribution.
    """
    now = timezone.now()
    today = now.date()

    # Current 7-day window vs previous 7-day window
    d7_start = today - timedelta(days=7)
    d14_start = today - timedelta(days=14)

    curr_sales = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=d7_start
    )
    prev_sales = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=d14_start,
        created_at__date__lt=d7_start
    )

    curr_rev = curr_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    prev_rev = prev_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    curr_count = curr_sales.count()
    prev_count = prev_sales.count()

    insights = []

    # 1. Revenue trajectory
    if prev_rev > 0:
        pct_change = ((curr_rev - prev_rev) / prev_rev) * 100
        if pct_change >= 5:
            insights.append({
                "type": "positive",
                "icon": "bi-graph-up-arrow",
                "title": f"Revenue Momentum (+{pct_change:.1f}%)",
                "text": f"Last 7-day revenue (₹{curr_rev:,.2f}) is outperforming previous week (₹{prev_rev:,.2f}) by {pct_change:+.1f}% across {curr_count} orders."
            })
        elif pct_change <= -5:
            insights.append({
                "type": "warning",
                "icon": "bi-graph-down-arrow",
                "title": f"Revenue Dip ({pct_change:.1f}%)",
                "text": f"Sales decreased from ₹{prev_rev:,.2f} to ₹{curr_rev:,.2f}. Consider reviewing footfall or stock availability."
            })
        else:
            insights.append({
                "type": "neutral",
                "icon": "bi-dash-circle",
                "title": "Steady Revenue Pace",
                "text": f"7-day sales remain steady at ₹{curr_rev:,.2f} ({pct_change:+.1f}% change WoW)."
            })
    else:
        insights.append({
            "type": "neutral",
            "icon": "bi-bar-chart",
            "title": "Baseline Performance",
            "text": f"7-day revenue stands at ₹{curr_rev:,.2f} across {curr_count} completed orders."
        })

    # 2. Payment Method Insight
    if curr_count > 0:
        payments_qs = Payment.objects.filter(sale__company=company, sale__in=curr_sales)
        upi_rev = payments_qs.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        cash_rev = payments_qs.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        card_rev = payments_qs.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        dominant = "UPI"
        dom_val = upi_rev
        if cash_rev > dom_val:
            dominant = "Cash"
            dom_val = cash_rev
        if card_rev > dom_val:
            dominant = "Card"
            dom_val = card_rev

        dom_pct = (dom_val / curr_rev * 100) if curr_rev > 0 else 0
        insights.append({
            "type": "info",
            "icon": "bi-credit-card-2-front",
            "title": f"Primary Tender: {dominant} ({dom_pct:.0f}%)",
            "text": f"Customers predominantly paid via {dominant} (₹{dom_val:,.2f}). Cash: ₹{cash_rev:,.2f}, UPI: ₹{upi_rev:,.2f}, Card: ₹{card_rev:,.2f}."
        })

    # 3. Top item velocity
    top_item = SaleItem.objects.filter(
        sale__company=company,
        sale__sale_status="COMPLETED",
        sale__created_at__date__gte=d7_start
    ).values("product_name").annotate(qty=Sum("quantity")).order_by("-qty").first()

    if top_item:
        insights.append({
            "type": "star",
            "icon": "bi-award",
            "title": "Star Product of the Week",
            "text": f"'{top_item['product_name']}' is leading demand with {top_item['qty']} units sold in the last 7 days."
        })

    return {
        "curr_rev": float(curr_rev),
        "prev_rev": float(prev_rev),
        "curr_count": curr_count,
        "insights": insights
    }


def generate_inventory_intelligence(company):
    """
    Inventory demand estimation and stock depletion projection.
    Estimates daily burn rate using past 30 days of sales history.
    """
    today = timezone.now().date()
    d30_start = today - timedelta(days=30)

    # Calculate 30-day unit sales per product
    recent_item_sales = SaleItem.objects.filter(
        sale__company=company,
        sale__sale_status="COMPLETED",
        sale__created_at__date__gte=d30_start
    ).values("product_id").annotate(units_sold=Sum("quantity"))

    sales_map = {item["product_id"]: float(item["units_sold"] or 0) for item in recent_item_sales}

    # Fetch active products with low or critical stock
    active_prods = list(Product.objects.filter(company=company, status=True).order_by("current_stock")[:20])

    recommendations = []
    for prod in active_prods:
        current_stock = float(prod.current_stock or 0)
        min_stock = float(prod.minimum_stock or 5)
        if current_stock > min_stock + 10:
            continue

        units_30d = sales_map.get(prod.id, 0.0)
        daily_velocity = units_30d / 30.0

        if daily_velocity > 0:
            days_runway = current_stock / daily_velocity
            urgency = "CRITICAL" if days_runway <= 3 else ("HIGH" if days_runway <= 7 else "MEDIUM")
            suggested_order = max(10, int((daily_velocity * 30) - current_stock + min_stock))
            recommendations.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.product_code,
                "current_stock": current_stock,
                "daily_velocity": round(daily_velocity, 2),
                "estimated_runway_days": round(days_runway, 1),
                "suggested_order": suggested_order,
                "urgency": urgency,
                "reason": f"At {daily_velocity:.1f} units/day, current stock will run out in ~{days_runway:.1f} days."
            })
        elif current_stock <= 0:
            recommendations.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.product_code,
                "current_stock": 0,
                "daily_velocity": 0.0,
                "estimated_runway_days": 0.0,
                "suggested_order": int(min_stock or 10),
                "urgency": "CRITICAL",
                "reason": "Completely out of stock. Customers cannot purchase this item."
            })

        if len(recommendations) >= 6:
            break

    return recommendations


def answer_business_question(company, question_text):
    """
    Answer natural-language business questions using authoritative,
    read-only queries against the tenant's data.
    Never executes arbitrary SQL or modifies data.
    """
    q = (question_text or "").strip().lower()
    today = timezone.now().date()
    q_clean = re.sub(r"[^\w\s]", "", q)

    # 1. Today's Sales
    if any(k in q_clean for k in ["sell today", "sales today", "today sales", "revenue today", "today revenue", "sold today"]):
        sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date=today)
        count = sales_qs.count()
        rev = sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
        cash = payments_qs.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        upi = payments_qs.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        card = payments_qs.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        answer = (
            f"Today ({today.strftime('%b %d, %Y')}), your store completed **{count} sales** "
            f"generating **₹{rev:,.2f}** in gross revenue.\n\n"
            f"**Payment Breakdown:**\n"
            f"• UPI: ₹{upi:,.2f}\n"
            f"• Cash: ₹{cash:,.2f}\n"
            f"• Card: ₹{card:,.2f}"
        )
        return {
            "intent": "sales_today",
            "answer": answer,
            "data": {"count": count, "revenue": float(rev), "upi": float(upi), "cash": float(cash), "card": float(card)}
        }

    # 2. Week over Week Comparison
    elif any(k in q_clean for k in ["compare this week with last week", "this week with last week", "week comparison", "weekly sales", "week over week", "last week comparison"]):
        d7_start = today - timedelta(days=7)
        d14_start = today - timedelta(days=14)

        this_week = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=d7_start)
        last_week = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=d14_start, created_at__date__lt=d7_start)

        rev_this = this_week.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        rev_last = last_week.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        count_this = this_week.count()
        count_last = last_week.count()

        if rev_last > 0:
            diff_pct = ((rev_this - rev_last) / rev_last) * 100
            trend_str = f"an increase of +{diff_pct:.1f}%" if diff_pct >= 0 else f"a decline of {diff_pct:.1f}%"
        else:
            trend_str = "no prior baseline for last week"

        answer = (
            f"📊 **Weekly Sales Comparison:**\n\n"
            f"• **This Week (Last 7 Days):** ₹{rev_this:,.2f} across {count_this} orders\n"
            f"• **Last Week (Prior 7 Days):** ₹{rev_last:,.2f} across {count_last} orders\n"
            f"• **Trend:** Revenue shows {trend_str}."
        )
        return {
            "intent": "weekly_comparison",
            "answer": answer,
            "data": {"this_week_rev": float(rev_this), "last_week_rev": float(rev_last), "this_week_count": count_this, "last_week_count": count_last}
        }

    # 3. Top Products
    elif any(k in q_clean for k in ["top products", "best sellers", "best selling", "most popular", "highest selling"]):
        top_items = SaleItem.objects.filter(
            sale__company=company,
            sale__sale_status="COMPLETED"
        ).values("product_name", "product_code").annotate(
            total_qty=Sum("quantity"),
            total_rev=Sum("total")
        ).order_by("-total_qty")[:5]

        if not top_items:
            return {"intent": "top_products", "answer": "No completed sales found yet to rank top products.", "data": []}

        lines = ["🏆 **Top 5 Best-Selling Products by Volume:**\n"]
        for idx, item in enumerate(top_items, 1):
            name = item["product_name"] or "Unknown"
            sku = item["product_code"] or "—"
            qty = item["total_qty"] or 0
            rev = item["total_rev"] or Decimal("0.00")
            lines.append(f"{idx}. **{name}** (`{sku}`) — **{qty} units** sold (₹{rev:,.2f})")

        return {"intent": "top_products", "answer": "\n".join(lines), "data": list(top_items)}

    # 4. Low Stock / Out of Stock
    elif any(k in q_clean for k in ["low on stock", "low stock", "running out", "out of stock", "reorder", "stock alert"]):
        low_prods = Product.objects.filter(
            company=company,
            status=True,
            current_stock__lte=5
        ).order_by("current_stock")[:10]

        if not low_prods.exists():
            return {
                "intent": "low_stock",
                "answer": "✅ **All Healthy!** There are currently no active products at or below low-stock threshold in your store inventory.",
                "data": []
            }

        lines = [f"⚠️ **Found {len(low_prods)} Products with Low Stock:**\n"]
        for item in low_prods:
            status_str = "🚨 OUT OF STOCK" if item.current_stock <= 0 else f"⚠️ {item.current_stock} left"
            lines.append(f"• **{item.name}** (`{item.product_code}`): {status_str} (Min threshold: {item.minimum_stock})")
        lines.append("\n*Recommendation: Contact suppliers to replenish these items to prevent checkout rejections.*")

        return {"intent": "low_stock", "answer": "\n".join(lines), "data": [{"sku": p.product_code, "name": p.name, "stock": float(p.current_stock)} for p in low_prods]}

    # 5. Demand Estimation & Forecasting
    elif any(k in q_clean for k in ["estimate demand", "demand", "forecast", "runway", "depletion"]):
        recs = generate_inventory_intelligence(company)
        if not recs:
            return {
                "intent": "demand_estimation",
                "answer": "Inventory is in a healthy equilibrium; no products are projected to stock out within the next 14 days.",
                "data": []
            }

        lines = ["📈 **AI Demand Forecast & Runway Projections:**\n"]
        for r in recs[:5]:
            lines.append(f"• **{r['product_name']}**: {r['reason']} Suggested reorder: **{r['suggested_order']} units**.")

        return {"intent": "demand_estimation", "answer": "\n".join(lines), "data": recs}

    # 6. Returns & Refunds
    elif any(k in q_clean for k in ["returns", "refunds", "return requests"]):
        returns_all = ReturnRequest.objects.filter(company=company)
        pending = returns_all.filter(status="PENDING").count()
        approved = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).count()
        rejected = returns_all.filter(status="REJECTED").count()
        refunded = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).aggregate(total=Sum("refund_amount"))["total"] or Decimal("0.00")

        answer = (
            f"🔄 **Returns & Refund Summary:**\n\n"
            f"• Pending Manager Review: **{pending} requests**\n"
            f"• Approved & Restocked: **{approved} requests**\n"
            f"• Rejected: **{rejected} requests**\n"
            f"• Total Refund Amount: **₹{refunded:,.2f}**"
        )
        return {"intent": "returns_summary", "answer": answer, "data": {"pending": pending, "approved": approved, "refunded": float(refunded)}}

    # Fallback / General
    else:
        sales_today = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date=today).aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        low_count = Product.objects.filter(company=company, status=True, current_stock__lte=5).count()

        answer = (
            f"🤖 **Store Summary for {company.company_name}:**\n"
            f"Today's revenue is **₹{sales_today:,.2f}**, and **{low_count} items** currently need stock attention.\n\n"
            f"You can ask me specific questions like:\n"
            f"• *\"How much did we sell today?\"*\n"
            f"• *\"Compare this week with last week.\"*\n"
            f"• *\"What are our top products?\"*\n"
            f"• *\"Which products are low on stock?\"*\n"
            f"• *\"Estimate demand for our products.\"*"
        )
        return {
            "intent": "general_overview",
            "answer": answer,
            "data": {"today_sales": float(sales_today), "low_stock_count": low_count}
        }
