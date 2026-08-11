---
name: booking-analyst
description: Analyzes diagnostic lab booking data, including customer trends, revenue, package performance, and operational metrics.
---

This skill is designed to analyze diagnostic lab booking data from the `bookings` table. It can answer questions related to customer bookings, revenue generation, package popularity, lab partner performance, and operational efficiency such as cancellation rates.

### Tool Workflow:

1.  **Understand the Schema**: If you are unsure about the available data or columns, start by calling `describe_schema` to understand the dataset structure and column meanings.

2.  **Utilize Standard KPIs**: For common business metrics, prefer using the `get_kpi` tool. The following standard KPIs are available:
    *   `total_revenue`: Calculates the total revenue generated from completed bookings.
    *   `repeat_customer_rate`: Determines the percentage of bookings made by repeat customers.
    *   `cancellation_rate`: Calculates the percentage of bookings that were cancelled.
    *   `monthly_revenue_trend`: Shows the total revenue grouped by month of the booking date for completed bookings.
    *   `bookings_by_city`: Provides the count of bookings and total revenue, grouped by city.

3.  **Execute Ad-Hoc Queries**: For questions that cannot be answered by the standard KPIs, use the `run_safe_query` tool. When using `run_safe_query`, always explain the SQL query you are about to execute before calling the tool.

    **Important Guardrails:**
    *   Never expose `phone` or `customer_name` in any aggregate outputs or dashboards.
    *   All data access must be read-only (no INSERT, UPDATE, DELETE, or DDL operations).
    *   Only reference columns that exist in the dataset schema: `booking_id`, `customer_id`, `customer_name`, `phone`, `city`, `package_name`, `package_category`, `lab_partner`, `booking_date`, `report_delivery_date`, `amount_inr`, `payment_status`, `is_repeat_customer`, `household_id`, `age`, `gender`, `status`.
    *   The table name for all queries is `bookings`.
    *   Limit ad-hoc query results to a maximum of 200 rows.

### Examples:

*   **User Question**: "What was the total revenue from completed bookings last month?"
    *   **Approach**: This is a standard KPI. I will use `get_kpi` with `kpi_name='monthly_revenue_trend'` and specify the relevant month if the tool supports date filtering, or calculate from the trend data.

*   **User Question**: "What is our current repeat customer rate?"
    *   **Approach**: This is a standard KPI. I will use `get_kpi` with `kpi_name='repeat_customer_rate'`.

*   **User Question**: "Show me the top 3 lab partners by total revenue generated from 'Full Body Checkup Advanced' packages."
    *   **Approach**: This requires an ad-hoc query. I will use `run_safe_query` to select `lab_partner` and sum `amount_inr`, filtering by `package_name='Full Body Checkup Advanced'` and `status='Completed'`, grouping by `lab_partner`, ordering by total amount, and limiting to 3. I will explain the query before execution.