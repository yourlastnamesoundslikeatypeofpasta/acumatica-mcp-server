---
name: three-way-match
description: Perform an accounts-payable three-way match in Acumatica - reconcile a vendor Bill against its Purchase Receipt and Purchase Order at the line level and flag price and quantity variances. Use when the user wants to validate a bill before release, check that a bill matches what was ordered and received, audit AP for over-billing or quantity mismatches, find bills with no linked PO or receipt, or run a procure-to-pay matching report. Walks bill lines to their PO and receipt via the PO order and receipt reference fields, compares billed quantity and unit cost to ordered and received values, and reports matched, variance, and unmatched lines. Read-only - it reports discrepancies and links each document; it does not modify or release anything.
---

# Acumatica - Three-Way Match (Bill <-> Receipt <-> PO)

Reconcile what was **ordered** (PO) vs **received** (receipt) vs **billed** (bill), line by line, and
flag variances before a bill is paid. Read-only - never releases or edits a document.

## ⚠️ Known tenant behaviour

- **`$orderby` is silently ignored** - sort client-side.
- Date filters need `datetimeoffset'YYYY-MM-DDTHH:MM:SS-04:00'` literals.
- `$select`/`$filter` field names must match exactly or you get a hard 500.
- Decimal comparison filters (e.g. `gt 0`) may 500 with "Type conversions not supported" - filter
  client-side instead.
- Render each document number as a Markdown link from its `browser_url`.

## The linking chain (verified field names)

Each **Bill** line (`Bill` -> `expand="Details"`) carries the back-references:
`POOrderType, POOrderNbr, POLine` -> the originating PO line, and
`POReceiptType, POReceiptNbr, POReceiptLine` -> the receipt line. Line amounts: `Qty, UnitCost,
ExtendedCost, Amount, InventoryID, LineType`.

Each **PurchaseReceipt** line (`PurchaseReceipt` -> `expand="Details"`) carries:
`POOrderType, POOrderNbr, POLineNbr` (link to PO line), `ReceiptQty, OrderedQty, OpenQty, UnitCost,
ExtendedCost, InventoryID, POReceiptNbr, POReceiptLineNbr`.

Each **PurchaseOrder** line (`PurchaseOrder` -> `expand="Details"`) carries:
`LineNbr, InventoryID, OrderQty, QtyOnReceipts, UnitCost, ExtendedCost, Completed`.

> Goods lines carry these PO/receipt references. Non-goods bill lines (e.g. accrued warranty, freight,
> GL-only) have the reference fields **empty** - those are legitimately "no PO" and should be reported
> as unmatched-by-design, not as errors.

## How to match a single bill

1. `get_record(entity="Bill", id="Bill/<RefNbr>", expand="Details")`.
2. For each bill line with a `POOrderNbr`, pull the PO once:
   `get_record(entity="PurchaseOrder", id="<POOrderType>/<POOrderNbr>", expand="Details")` and find the
   line where `LineNbr == bill.POLine`.
3. For each bill line with a `POReceiptNbr`, pull the receipt:
   `get_record(entity="PurchaseReceipt", id="<POReceiptType>/<POReceiptNbr>", expand="Details")` and find
   the line where its `POReceiptLineNbr`/`LineNbr` matches `bill.POReceiptLine`.
4. Compare per line (see rules) and classify each as **Match**, **Variance**, or **Unmatched**.

Cache PO/receipt lookups - many bill lines share one PO.

## Match rules (per bill line)

- **Quantity:** `bill.Qty` should equal the receipt line `ReceiptQty`. Bill qty != received qty -> variance.
- **Price:** `bill.UnitCost` should equal `PO.UnitCost` (and the receipt `UnitCost`). Tolerance is 0 by
  default; if the user gives a tolerance (e.g. 2%), apply it.
- **Extended:** `bill.ExtendedCost` vs `PO line ExtendedCost` for the billed qty.
- **Over-bill:** billed qty across all bills for a PO line exceeds `OrderQty` -> over-billing flag.
- **Unmatched:** bill line has no `POOrderNbr` and no `POReceiptNbr` and is a goods line -> unmatched
  (true GL/expense lines are expected and noted separately).

## Bulk mode - find exception bills in a window

To audit rather than check one bill:
```
list_records(entity="Bill",
  filter="Status eq 'Pending Approval' and Date gt datetimeoffset'<start>'",
  expand="Details", select="Type,ReferenceNbr,Vendor,Amount,Status,Date", top=200)
```
Then run the per-line rules on each; report only bills with at least one variance or unmatched goods line.

## Output format

```
Bill: [010042](url)  Vendor 100234  Amount 4,150.58  Status Pending Approval
  Line 1  INV ABC-12345  Qty 3  @ 245.35   PO P010001/1 (ord 245.35)  R010001/1 (recv 3)  ✅ Match
  Line 2  INV ...            Qty 5  @ 12.00    PO P010001/2 (ord 11.50)   ⚠ Price variance +0.50/ea
  Line 3  Accrued Warranty   Qty 1  @ 4150.58  (no PO - GL line)          ℹ Unmatched by design
Result: 1 match, 1 price variance, 1 GL line. Recommend reviewing line 2 before release.
```

## Tips

- A clean three-way match is a precondition for releasing a bill - surface variances *before* anyone
  hits Release.
- Hand any single problem document to `doc-doctor` for a deeper why-is-this-stuck diagnosis.
- For approval-state and on-hold context across many bills, pair with `p2p-monitor`.
