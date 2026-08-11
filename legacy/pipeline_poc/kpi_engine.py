"""KPI computation engine — used in validation dry-run and packaged MCP server."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

TABLE_NAME = "bookings"

# Proven implementations for industry-pack KPIs
BUILTIN_COMPUTERS: set[str] = {
    "total_revenue",
    "repeat_customer_rate",
    "cancellation_rate",
    "monthly_revenue_trend",
    "bookings_by_city",
    "number_of_repeat_customers",
    "revenue_by_lab_partner_and_package",
}


def compute_kpi(df: pd.DataFrame, kpi_name: str) -> dict[str, Any]:
    name = kpi_name.strip().lower().replace("-", "_").replace(" ", "_")

    if name == "total_revenue":
        completed = df[df["status"] == "Completed"]
        return {
            "kpi": name,
            "value_inr": int(completed["amount_inr"].sum()),
            "completed_bookings": len(completed),
        }
    if name == "repeat_customer_rate":
        yes = int((df["is_repeat_customer"] == "Yes").sum())
        total = len(df)
        return {
            "kpi": name,
            "percent": round(yes / total * 100, 2),
            "repeat_bookings": yes,
            "total_bookings": total,
        }
    if name == "cancellation_rate":
        cancelled = int((df["status"] == "Cancelled").sum())
        total = len(df)
        return {
            "kpi": name,
            "percent": round(cancelled / total * 100, 2),
            "cancelled_bookings": cancelled,
            "total_bookings": total,
        }
    if name == "bookings_by_city":
        grouped = (
            df.groupby("city", as_index=False)
            .agg(booking_count=("booking_id", "count"), revenue_inr=("amount_inr", "sum"))
            .sort_values("revenue_inr", ascending=False)
        )
        return {"kpi": name, "cities": grouped.to_dict(orient="records")}
    if name == "monthly_revenue_trend":
        completed = df[df["status"] == "Completed"].copy()
        completed["month"] = pd.to_datetime(completed["booking_date"]).dt.to_period("M").astype(str)
        grouped = (
            completed.groupby("month", as_index=False)
            .agg(revenue_inr=("amount_inr", "sum"), booking_count=("booking_id", "count"))
            .sort_values("month")
        )
        return {"kpi": name, "months": grouped.to_dict(orient="records")}
    if name == "number_of_repeat_customers":
        count = int(df.loc[df["is_repeat_customer"] == "Yes", "customer_id"].nunique())
        return {"kpi": name, "repeat_customers": count}
    if name == "revenue_by_lab_partner_and_package":
        completed = df[df["status"] == "Completed"]
        grouped = (
            completed.groupby(["lab_partner", "package_name"], as_index=False)
            .agg(revenue_inr=("amount_inr", "sum"), booking_count=("booking_id", "count"))
            .sort_values("revenue_inr", ascending=False)
        )
        return {"kpi": name, "breakdown": grouped.to_dict(orient="records")}

    raise ValueError(f"Unsupported KPI: {kpi_name}")


def extract_kpi_names(tool_specs: dict) -> list[str]:
    names: list[str] = []
    for tool in tool_specs.get("tools", []):
        if tool.get("name") == "get_kpi":
            for kpi in tool.get("kpis", []):
                if kpi.get("name"):
                    names.append(kpi["name"])
    return names


def dry_run_kpis(df: pd.DataFrame, tool_specs: dict) -> list[dict]:
    results = []
    for name in extract_kpi_names(tool_specs):
        entry: dict[str, Any] = {"kpi_name": name, "status": "pass"}
        try:
            if name not in BUILTIN_COMPUTERS:
                entry["status"] = "warn"
                entry["message"] = f"KPI '{name}' is not in proven builtin set; attempting compute"
            value = compute_kpi(df, name)
            entry["result"] = value
        except Exception as exc:
            entry["status"] = "fail"
            entry["error"] = str(exc)
        results.append(entry)
    return results
