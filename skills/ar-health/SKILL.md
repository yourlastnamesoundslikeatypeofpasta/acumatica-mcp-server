---
name: ar-health
description: Run an accounts receivable health check. Use when the user asks "how's our AR?", "who owes us money?", "what's our outstanding balance?", "show me overdue invoices", "are there unapplied payments?", or wants an AR aging summary.
---

# Acumatica - AR Health Check

A multi-query accounts receivable audit. Run all steps and compile into a single report.

## Acumatica mechanics (read first)

- **Field names are never guessed.** Use only names from `describe_entity` (or the exact
  queries below). A wrong name in `filter=`/`select=` returns HTTP 500 `KeyNotFoundException`.
- **`$orderby` is silently ignored.** For "most recent", filter a narrow date window and widen
  (14 -> 30 -> 90 days), then sort client-side:
  `filter="Date gt datetimeoffset'2026-06-22T00:00:00-04:00'"` (compute the date from today).
- **Decimal literals fail in filters.** `Amount gt 0` returns "Type conversions are not
  supported" - drop the numeric clause and filter client-side.
- **Paginate.** Default `top` is 50. If a call returns exactly `top` rows, repeat with
  `skip=` until a short page comes back - never report a full page as the complete total.
- **Large results may arrive as a file path** instead of inline JSON. Read the file and
  aggregate (jq / Python); do not re-run the query hoping it shrinks.
- **Always pass `select=`** with only the fields you need. StockItem and ItemWarehouse are
  enormous without it.
- **Link every document.** Render the primary identifier (RefNbr / OrderNbr / ID) as a
  Markdown hyperlink using the record's `browser_url` field.
- **On any error, read the `hint` field** in the tool response - the server translates
  common 500s into the corrective action.
- **Invoice `DueDate` filters can be dropped silently** by the server - if you filter on
  `DueDate`, verify the returned rows actually honor the bound (or query `Bill` instead for AP).
  This skill avoids the problem by pulling open invoices and bucketing by `DueDate` client-side.
- **Payment's date field is `ApplicationDate`, not `Date`** - filtering `Payment` on `Date`
  returns 500.

---

## Step 1 - Open invoices (AR balance)

```
list_records(
    entity="Invoice",
    filter="Type eq 'Invoice' and Status eq 'Open'",
    select="ReferenceNbr,Customer,Date,DueDate,Amount,Balance,Description",
    top=1000
)
```

From results, compute client-side:
- **Total open AR balance** = sum of all `Balance` values
- **Aging buckets** by `DueDate` relative to today:
  - Current (not yet due)
  - 1-30 days overdue
  - 31-60 days overdue
  - 61-90 days overdue
  - 90+ days overdue
- **Top 10 largest open balances** (customer + amount)

---

## Step 2 - Unapplied / open payments

```
list_records(
    entity="Payment",
    filter="Type eq 'Payment' and Status eq 'Open'",
    select="ReferenceNbr,CustomerID,ApplicationDate,PaymentAmount,PaymentMethod,Description",
    top=200
)
```

These are received payments not yet applied to an invoice - may reduce net AR.
Total = sum of `PaymentAmount`.

---

## Step 3 - Open prepayments / deposits

```
list_records(
    entity="Payment",
    filter="Type eq 'Prepayment' and Status eq 'Open'",
    select="ReferenceNbr,CustomerID,ApplicationDate,PaymentAmount,Description",
    top=200
)
```

Deposits on file from customers - expected to offset future invoices.
Total = sum of `PaymentAmount`.

---

## Step 4 - Open credit memos

```
list_records(
    entity="Invoice",
    filter="Type eq 'Credit Memo' and Status eq 'Open'",
    select="ReferenceNbr,Customer,Date,Amount,Balance,Description",
    top=200
)
```

Pending credits that will reduce AR when applied.

---

## Step 5 - Customer concentration (top debtors)

From the Step 1 results, group open balances by `Customer` and rank by total balance. Identify:
- Top 5 customers by outstanding balance
- Any single customer representing >20% of total AR (concentration risk)

---

## Output format

```
AR HEALTH REPORT - [today's date]
==================================================

TOTAL OPEN AR:           $[amount]   ([N] invoices)
Unapplied Payments:     -$[amount]   ([N] payments on file)
Open Prepayments:       -$[amount]   ([N] deposits on file)
Open Credit Memos:      -$[amount]   ([N] credits pending)
--------------------------------------------------
NET AR EXPOSURE:         $[amount]

AGING BREAKDOWN:
  Current (not due):     $[amount]  ([N] invoices)
  1-30 days overdue:     $[amount]  ([N] invoices)
  31-60 days overdue:    $[amount]  ([N] invoices)
  61-90 days overdue:    $[amount]  ([N] invoices)
  90+ days overdue:      $[amount]  ([N] invoices) ⚠️

TOP 5 OUTSTANDING BALANCES:
  1. Customer [ID/Name]  $[balance]  ([days] days outstanding)
  2. ...

⚠️  ANOMALIES:
  - [N] invoices 90+ days overdue (total: $[amount])
  - [N] unapplied payments sitting idle
  - Customer [X] has [N] separate open invoices - possible billing issue
```

## Tips

- Update date literals to today before running.
- `Balance` on an Invoice is the remaining unpaid amount; `Amount` is the original total.
- `Customer` on the Invoice entity is the CustomerID string.
- After identifying high-balance customers, use `get_record(entity="Customer", id="<CustomerID>")` to get their name and contact info.
- Net AR Exposure = Total Open AR - Unapplied Payments - Open Prepayments - Open Credit Memos.
