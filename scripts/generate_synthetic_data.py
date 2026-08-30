"""Synthetic Dataset Generator for MIS Plugin Forge & MCP Runtime Testing.

Generates realistic business datasets with natural distributions, categorical fields,
temporal trends, measures, and PII fields to test all 4 MCP tiers and validation checks.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Seed for reproducibility
random.seed(42)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "datasets"


def generate_edtech_leads(n: int = 1000) -> Path:
    """Generate realistic EdTech Lead Generation & Trial Booking dataset."""
    first_names = ["Aarav", "Aditi", "Ananya", "Dev", "Ishaan", "Kavya", "Mira", "Neha", "Rahul", "Riya", "Rohan", "Sanya", "Tanvi", "Varun", "Zoya"]
    last_names = ["Sharma", "Verma", "Patel", "Mehta", "Iyer", "Nair", "Reddy", "Gupta", "Malhotra", "Kapoor", "Bhatia", "Joshi", "Chopra", "Das"]
    courses = ["Python for AI", "Data Science Bootcamp", "Full-Stack Web Dev", "Machine Learning Mastery", "Cloud DevOps Pro", "UI/UX Design Masterclass"]
    campaigns = ["google_search_q1", "meta_leads_spring", "youtube_creator_sponsor", "linkedin_professionals", "organic_referral"]
    agents = ["Agent-Vikram", "Agent-Pooja", "Agent-Amit", "Agent-Sneha", "Agent-Karan"]
    statuses = ["Completed", "Trial Booked", "Callback Requested", "Not Interested", "Lost", "Invalid Number"]
    status_weights = [0.35, 0.25, 0.15, 0.12, 0.08, 0.05]
    cities = ["Mumbai", "Bengaluru", "Delhi", "Hyderabad", "Pune", "Chennai", "Kolkata", "Ahmedabad", "Jaipur", "Chandigarh"]

    base_date = datetime(2026, 1, 1)
    file_path = OUTPUT_DIR / "synthetic_edtech_leads.csv"

    fieldnames = [
        "lead_id",
        "student_name",
        "email",
        "phone",
        "course_name",
        "campaign_id",
        "sales_rep",
        "city",
        "status",
        "call_duration_mins",
        "lead_score",
        "course_fee_inr",
        "amount_paid_inr",
        "discount_applied_inr",
        "created_at",
        "trial_date",
    ]

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i in range(1, n + 1):
            fname = random.choice(first_names)
            lname = random.choice(last_names)
            course = random.choice(courses)
            campaign = random.choice(campaigns)
            agent = random.choice(agents)
            status = random.choices(statuses, weights=status_weights)[0]
            city = random.choice(cities)

            # Dates
            created_days = random.randint(0, 75)
            created_dt = base_date + timedelta(days=created_days, hours=random.randint(8, 20), minutes=random.randint(0, 59))
            trial_dt = created_dt + timedelta(days=random.randint(1, 5)) if status in ("Trial Booked", "Completed") else ""

            # Fee & Payments
            base_fee = random.choice([25000, 35000, 45000, 60000, 75000])
            discount = random.choice([0, 2000, 5000, 10000]) if status == "Completed" else 0
            if status == "Completed":
                amount_paid = base_fee - discount
            elif status == "Trial Booked":
                amount_paid = random.choice([0, 1000, 2000]) # Token deposit
            else:
                amount_paid = 0

            lead_score = random.randint(20, 99) if status != "Invalid Number" else random.randint(1, 15)
            call_mins = round(random.uniform(2.5, 35.0), 1) if status in ("Completed", "Trial Booked") else round(random.uniform(0.5, 8.0), 1)

            writer.writerow({
                "lead_id": f"LEAD-{10000 + i}",
                "student_name": f"{fname} {lname}",
                "email": f"{fname.lower()}.{lname.lower()}{random.randint(10, 99)}@example.com",
                "phone": f"+91-98{random.randint(10000000, 99999999)}",
                "course_name": course,
                "campaign_id": campaign,
                "sales_rep": agent,
                "city": city,
                "status": status,
                "call_duration_mins": call_mins,
                "lead_score": lead_score,
                "course_fee_inr": base_fee,
                "amount_paid_inr": amount_paid,
                "discount_applied_inr": discount,
                "created_at": created_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "trial_date": trial_dt.strftime("%Y-%m-%d") if trial_dt else "",
            })

    print(f"Generated {n} records to {file_path}")
    return file_path


def generate_ecommerce_orders(n: int = 1000) -> Path:
    """Generate realistic E-commerce & Retail Orders dataset."""
    first_names = ["Emma", "Liam", "Olivia", "Noah", "Ava", "Oliver", "Sophia", "Elijah", "Charlotte", "Lucas"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
    categories = ["Electronics", "Fashion & Apparel", "Home & Kitchen", "Books & Media", "Sports & Fitness", "Beauty & Care"]
    channels = ["Mobile App", "Desktop Web", "Direct", "Affiliate Partner", "Instagram Ad"]
    statuses = ["Delivered", "Shipped", "Processing", "Cancelled", "Returned"]
    status_weights = [0.65, 0.15, 0.08, 0.07, 0.05]
    regions = ["North America", "Europe", "Asia-Pacific", "Latin America", "Middle East"]
    payments = ["Credit Card", "UPI", "PayPal", "Apple Pay", "Cash on Delivery"]

    base_date = datetime(2026, 1, 1)
    file_path = OUTPUT_DIR / "synthetic_ecommerce_orders.csv"

    fieldnames = [
        "order_id",
        "customer_id",
        "customer_name",
        "customer_email",
        "product_category",
        "sales_channel",
        "region",
        "payment_method",
        "order_status",
        "units_ordered",
        "unit_price_usd",
        "gross_amount_usd",
        "discount_amount_usd",
        "net_revenue_usd",
        "shipping_cost_usd",
        "order_date",
        "delivery_date",
    ]

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i in range(1, n + 1):
            fname = random.choice(first_names)
            lname = random.choice(last_names)
            category = random.choice(categories)
            channel = random.choice(channels)
            status = random.choices(statuses, weights=status_weights)[0]
            region = random.choice(regions)
            payment = random.choice(payments)

            order_days = random.randint(0, 80)
            order_dt = base_date + timedelta(days=order_days, hours=random.randint(6, 23), minutes=random.randint(0, 59))
            delivery_dt = order_dt + timedelta(days=random.randint(2, 7)) if status in ("Delivered", "Returned") else ""

            units = random.choices([1, 2, 3, 4, 5], weights=[0.55, 0.25, 0.12, 0.05, 0.03])[0]
            if category == "Electronics":
                unit_price = round(random.uniform(80.0, 850.0), 2)
            elif category == "Fashion & Apparel":
                unit_price = round(random.uniform(25.0, 180.0), 2)
            else:
                unit_price = round(random.uniform(15.0, 120.0), 2)

            gross = round(units * unit_price, 2)
            discount = round(gross * random.choice([0.0, 0.05, 0.10, 0.15, 0.20]), 2)
            net = round(gross - discount, 2) if status not in ("Cancelled", "Returned") else 0.0
            shipping = round(random.choice([0.0, 4.99, 9.99, 14.99]), 2)

            writer.writerow({
                "order_id": f"ORD-{20000 + i}",
                "customer_id": f"CUST-{random.randint(100, 500)}",
                "customer_name": f"{fname} {lname}",
                "customer_email": f"{fname.lower()}.{lname.lower()}{random.randint(1, 99)}@testmail.com",
                "product_category": category,
                "sales_channel": channel,
                "region": region,
                "payment_method": payment,
                "order_status": status,
                "units_ordered": units,
                "unit_price_usd": unit_price,
                "gross_amount_usd": gross,
                "discount_amount_usd": discount,
                "net_revenue_usd": net,
                "shipping_cost_usd": shipping,
                "order_date": order_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "delivery_date": delivery_dt.strftime("%Y-%m-%d") if delivery_dt else "",
            })

    print(f"Generated {n} records to {file_path}")
    return file_path


if __name__ == "__main__":
    generate_edtech_leads(1000)
    generate_ecommerce_orders(1000)
