---
name: ap-health
description: Run an accounts payable health check. Use when the user asks "what do we owe?", "are any vendor bills overdue?", "show AP aging", "are there receipts we haven't been billed for?", "any old open POs?", or wants a full payables summary.
---

# Acumatica - AP Health Check

Run every step and produce one deterministic, reconciled report. Treat this
document as an executable specification: do not omit, reinterpret, or replace
queries, calculations, fallbacks, sections, or headline metrics.

## Mandatory execution rules

1. Use the current date in the user's timezone as the report date and aging date.
2. Call `describe_entity` before querying an entity if its fields or key format
   have not already been confirmed in the current session.
3. Run all four data queries. A failure in one query does not cancel the others.
4. Paginate every `list_records` query:
   - Run the first call with the specified `top` and `skip=0`.
   - If the page contains exactly `top` records, call the same query again with
     `skip` increased by `top`.
   - Continue until a page contains fewer than `top` records, including zero.
   - Combine pages and deduplicate by the response `id`; if `id` is absent, use
     the entity's business key.
   - If any later page fails, retain prior pages, mark all affected counts and
     amounts as `at least` / `lower bound`, and disclose the failed page and
     `skip` value.
5. Never treat a page containing exactly `top` records as a complete result.
6. Sort and aggregate client-side. Do not rely on `$orderby`; it is ignored on
   this tenant.
7. Use decimal arithmetic for money. Round only displayed values, not
   intermediate sums.
8. Format currency with a dollar sign, thousands separators, and exactly two
   decimals. Format percentages with exactly one decimal. Format counts as
   integers.
9. Render every cited Bill, Purchase Receipt, and Purchase Order identifier as
   a Markdown hyperlink using its `browser_url`. If absent, construct the link
   using the URL rules in the "Deep links" section.
10. Report every query failure, retry, changed filter, pagination action,
    truncation, deduplication, and incomplete result in `QUERY DEVIATIONS AND
    DATA COMPLETENESS`.
11. Do not make unsupported narrative claims such as "virtually all", "mostly",
    or "significant". Quantify the claim with a count, amount, or percentage, or
    omit it.
12. Additional interpretation not required by this skill must appear only in a
    separate `SUPPLEMENTAL ANALYSIS` section after the required report.

## Step 1 - Open AP bills

Run this query with the pagination rules above:

```text
list_records(
    entity="Bill",
    filter="Status eq 'Open'",
    select="ReferenceNbr,Vendor,Date,DueDate,Amount,Balance,Description",
    top=500,
    skip=<PAGE_OFFSET>
)
```

For the combined, deduplicated result:

- `Total open AP balance` = sum of `Balance`.
- `Open bill count` = number of records.
- Group by `Vendor` and calculate each vendor's bill count, balance, and share:
  `vendor balance / total open AP balance * 100`.
- Rank vendors by balance descending, then VendorID ascending as the tie-breaker.

### Aging rules

Calculate age using calendar dates only:

`days_overdue = report_date - DueDate`

Assign every open bill to exactly one bucket:

| Condition | Bucket |
|---|---|
| `DueDate` is missing/null/empty | No due date |
| `DueDate >= report_date` | Current (not yet due) |
| `days_overdue` is 1 through 30 | 1-30 days overdue |
| `days_overdue` is 31 through 60 | 31-60 days overdue |
| `days_overdue` is 61 through 90 | 61-90 days overdue |
| `days_overdue >= 91` | 90+ days overdue |

Do not place missing due dates in Current. A bill due today is Current.

### Mandatory aging reconciliation

Before reporting, verify both equations:

```text
sum(all six aging bucket balances) = total open AP balance
sum(all six aging bucket counts) = open bill count
```

If either equation fails, do not silently publish the report. Recalculate once.
If it still fails, label the report `RECONCILIATION FAILED`, show both
differences, and disclose the issue in the deviations section.

## Step 2 - Released receipts not yet billed

`UnbilledQuantity` cannot be used in an OData filter on this tenant. Run this
query exactly as written, with pagination:

```text
list_records(
    entity="PurchaseReceipt",
    filter="Type eq 'Receipt' and Status eq 'Released'",
    select="ReceiptNbr,VendorID,Date,TotalCost,TotalQty,UnbilledQuantity",
    top=500,
    skip=<PAGE_OFFSET>
)
```

Mandatory client-side processing:

1. Keep records where numeric `UnbilledQuantity > 0`.
2. `Unbilled receipt count` = number of retained records.
3. `Accrual exposure` = sum of `TotalCost` for retained records.

Never replace this query with an OData condition on `UnbilledQuantity`. If a
numeric OData filter is attempted and rejected, retry using the exact query
above and disclose the rejected attempt. Do not report "could not query" when
the unfiltered Released-receipts query succeeded.

If pagination is incomplete, label both the receipt count and exposure as
`at least` and `lower bound`; do not present them as complete.

## Step 3 - Stale open purchase orders

Compute `cutoff_date = report_date - 60 calendar days`. Use the tenant's UTC
offset for midnight on the cutoff date. For example, June 6, 2026 uses:
`datetimeoffset'2026-04-07T00:00:00-04:00'`.

Run with pagination:

```text
list_records(
    entity="PurchaseOrder",
    filter="Status eq 'Open' and Date lt datetimeoffset'<CUTOFF_DATE>T00:00:00<UTC_OFFSET>'",
    select="OrderNbr,VendorID,Date,PromisedOn,OrderTotal,Type,Description",
    top=200,
    skip=<PAGE_OFFSET>
)
```

Calculate:

- Stale PO count.
- Stale PO total = sum of `OrderTotal`.
- Oldest stale PO date = minimum `Date`.

The filter uses `Date lt cutoff_date`; a PO exactly 60 days old is not included.
Describe the result as "older than 60 days", not "60+ days".

## Step 4 - AP bills on hold

First run this query with pagination:

```text
list_records(
    entity="Bill",
    filter="Status eq 'On Hold' and Balance gt 0",
    select="ReferenceNbr,Vendor,Date,DueDate,Amount,Balance,Description",
    top=200,
    skip=<PAGE_OFFSET>
)
```

If and only if Acumatica rejects the numeric `Balance gt 0` filter, retry with:

```text
list_records(
    entity="Bill",
    filter="Status eq 'On Hold'",
    select="ReferenceNbr,Vendor,Date,DueDate,Amount,Balance,Description",
    top=200,
    skip=<PAGE_OFFSET>
)
```

For the fallback result, calculate:

- `All on-hold record count` = all returned On Hold records.
- `Positive-balance on-hold bills` = records where numeric `Balance > 0`.
- `Zero-balance records excluded` = records where `Balance <= 0` or missing.
- `On-hold balance` = sum of `Balance` for positive-balance records only.

When the original filtered query succeeds, the positive-balance count is known,
but the all-record and excluded-zero counts are not. Display those two values as
`not queried` rather than inventing them. When the fallback runs, headline
wording must show all three counts explicitly.

## Step 5 - Vendor name lookup

Collect unique VendorIDs from:

- The top 10 vendors by open balance.
- Every positive-balance on-hold bill.
- Any overdue bill or stale PO cited in the detailed anomaly section when its
  vendor is displayed.

For each unique VendorID, resolve its display name with a live lookup:

```text
get_record(
    entity="Vendor",
    id="<VendorID>",
    select="VendorID,VendorName"
)
```

Build `{ VendorID -> VendorName }`. Display vendors everywhere in this exact
order and punctuation:

```text
VendorID - VendorName
```

If a lookup fails, display `VendorID - name unavailable` and disclose the
failure. Do not reverse the order or use parentheses.

## Step 6 - Deterministic detailed anomaly selection

Use the following rules exactly.

### Vendor concentration

- Show the top five vendors by open balance.
- Show balance, bill count, and share of total AP for each.
- List every single vendor whose share is strictly greater than 25.0%.
- Also calculate the combined share of the top two vendors. This is context,
  not an additional risk threshold.

### 90+ day overdue bills

- Sort 90+ bills by `Balance` descending, then `DueDate` ascending, then
  `ReferenceNbr` ascending.
- Show exactly the first five, or all if fewer than five.
- For each, show linked ReferenceNbr, vendor, balance, due date, integer days
  overdue, and description.
- Show `... plus [N] more` when more than five exist.

### 31-90 day overdue

- Combine the 31-60 and 61-90 buckets.
- Show the combined balance and bill count.
- Do not list individual bills in this subsection.

### Stale purchase orders

Create two selections:

1. Three oldest: sort by `Date` ascending, then `OrderNbr` ascending.
2. Three largest: sort by `OrderTotal` descending, then `Date` ascending, then
   `OrderNbr` ascending.

Merge in that order and deduplicate by `Type + OrderNbr`. Show each selected PO
once with linked OrderNbr, vendor, date, promised date, order total, and
description. Show `... plus [N] other stale POs` where N is the total stale
count minus the number of unique POs displayed.

### Bills on hold

- Sort positive-balance bills by `Balance` descending, then `ReferenceNbr`
  ascending.
- If 10 or fewer exist, show every bill.
- If more than 10 exist, show the first 10 and `... plus [N] more`.
- Show linked ReferenceNbr, vendor, balance, due date or `no due date`, and
  description.

## Deep links

Always prefer the response `browser_url`.

If it is missing, construct:

```text
Bill:
https://example.acumatica.com/Main?CompanyID=YourCompany&ScreenId=AP301000&DocType=INV&RefNbr=<ReferenceNbr>

Purchase Receipt:
https://example.acumatica.com/Main?CompanyID=YourCompany&ScreenId=PO302000&ReceiptType=RT&ReceiptNbr=<ReceiptNbr>

Purchase Order:
https://example.acumatica.com/Main?CompanyID=YourCompany&ScreenId=PO301000&OrderType=<URL_TYPE>&OrderNbr=<OrderNbr>
```

For Purchase Order links, prefer `browser_url` because API display `Type` values
may require translation to Acumatica URL codes.

## Mandatory report template

Use this exact section order and labels. Do not omit a section. Replace bracketed
tokens with calculated values. Do not copy explanatory bracket text into the
report.

```text
AP HEALTH REPORT - [Month D, YYYY]
============================================================

TOTAL OPEN AP:               $[amount]   ([N] bills)
BILLS ON HOLD:               $[positive balance]
  All on-hold records:       [N or "not queried"]
  Positive-balance bills:    [N]
  Zero-balance excluded:     [N or "not queried"]
------------------------------------------------------------
ACCRUAL EXPOSURE:
  Unbilled receipts:         $[amount]   ([N] receipts)
  Completeness:              [Complete | Lower bound - reason]
STALE OPEN POS:
  Older than 60 days:        $[amount]   ([N] POs)
  Oldest PO date:            [date or "none"]

AGING BREAKDOWN - OPEN BILLS ONLY:
  Current (not yet due):     $[amount]   ([N] bills)
  1-30 days overdue:         $[amount]   ([N] bills)
  31-60 days overdue:        $[amount]   ([N] bills)
  61-90 days overdue:        $[amount]   ([N] bills)
  90+ days overdue:          $[amount]   ([N] bills)
  No due date:               $[amount]   ([N] bills)
  RECONCILIATION:            $[bucket sum] = $[open AP total];
                             [bucket count] bills = [open bill count] bills

TOP 5 VENDORS BY OPEN BALANCE:
  1. [VendorID - VendorName]  $[balance]  ([N] bills; [P]%)
  2. ...

CONCENTRATION:
  Vendors above 25.0%:       [list each vendor and percentage, or "None"]
  Top two combined:          [P]%

DETAILED ANOMALIES:

90+ DAY OVERDUE:
  Total: $[amount] across [N] bills
  1. [linked bill] | [VendorID - VendorName] | $[balance] |
     due [date] | [N] days overdue | [description]
  ...
  [... plus N more, when applicable]

31-90 DAY OVERDUE:
  Total: $[31-60 plus 61-90 amount] across [combined count] bills

STALE OPEN POS:
  Total: $[amount] across [N] POs older than 60 days
  [linked PO] | [VendorID - VendorName] | dated [date] |
  promised [date or "none"] | $[amount] | [description]
  ...
  [... plus N other stale POs, when applicable]

BILLS ON HOLD:
  Positive balance: $[amount] across [N] bills
  [linked bill] | [VendorID - VendorName] | $[balance] |
  due [date or "no due date"] | [description]
  ...
  [... plus N more, when applicable]

QUERY DEVIATIONS AND DATA COMPLETENESS:
  - [Every failure, retry, changed filter, pagination fact, cap,
     deduplication, and lower-bound limitation.]
  - [If none: "No query deviations. All pagination completed."]
```

### Empty-section wording

- No records: show `$0.00 across 0 [records]` and `None`.
- No vendor above 25.0%: show `None`.
- Failed query with no usable pages: show `Unavailable - query failed`; do not
  substitute zero.
- Partial query: prefix affected counts and amounts with `at least` and state
  `Lower bound` in completeness.

## Final pre-report checklist

Do not send the report until all applicable checks pass:

- All four query steps were attempted.
- Every full page was followed by the next `skip` page.
- Combined pages were deduplicated.
- Open AP count and balance were calculated from the same record set.
- Six aging bucket counts and balances reconcile to Open AP.
- Missing due dates are only in `No due date`.
- Receipt filtering was client-side.
- On-hold fallback and counts follow Step 4.
- Vendor names were resolved or marked unavailable.
- All cited document identifiers are links.
- Required anomaly selections and tie-breakers were followed.
- Deviations and completeness are explicit.

## Regression fixture - June 6, 2026

Use this only to validate implementation behavior against the known June 6,
2026 run. Never substitute these constants for live query results.

Expected values for that fixture:

```text
Total open AP: $4,305,391.54 across 442 bills
Current: $2,599,872.86 across 344 bills
1-30: $223,171.02 across 26 bills
31-60: $343,034.49 across 13 bills
61-90: $181,944.04 across 4 bills
90+: $695,236.07 across 43 bills
No due date: $262,133.06 across 12 bills
On-hold fallback: 8 total records
Positive-balance on hold: $6,248.06 across 3 bills
Zero-balance on hold excluded: 5 records
```

The six aging balances must sum to `$4,305,391.54`, and the six aging counts
must sum to `442`.
