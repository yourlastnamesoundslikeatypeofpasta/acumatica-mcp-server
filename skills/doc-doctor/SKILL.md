---
name: doc-doctor
description: Diagnose and fix stuck or erroring Acumatica transactional documents - purchase orders that will not receive, totals that look wrong, documents stuck on hold or in the wrong status. Use when someone reports a document problem by number (for example PO P010001 will not receive, or bill 010001 total is off), when a PO shows Completed but still has open quantity, when a receipt will not convert to a bill, or when asked why a document is stuck, blocked, or cannot be released. Pulls the document, its lines, and related documents, runs a rules-based diagnosis, explains the root cause in plain language, and proposes the corrective action. Read-only by default - any write (reopen, uncheck a line Completed flag, release, hold) is previewed and applied only after explicit confirmation.
---

# Acumatica - Document Doctor

Diagnose *why a transactional document is stuck or wrong*, then propose the fix. This is the tool for
the "can you look at PO P0xxxxx?" requests - receive failures, total mismatches, on-hold/blocked docs.

## ⚠️ Known tenant behaviour

- **`$orderby` is silently ignored** - sort client-side.
- Date filters need `datetimeoffset'YYYY-MM-DDTHH:MM:SS-04:00'` literals.
- `$select`/`$filter` field names must match exactly or you get a hard 500.
- Always render the document number as a Markdown link using the `browser_url` field returned.

## How to run a diagnosis

1. **Identify the entity** from the document number prefix when not stated:
   `P` -> PurchaseOrder, `R` -> PurchaseReceipt, numeric AP ref (e.g. `010001`) -> Bill,
   `O`/`I`/`QT` -> SalesOrder, transfers/issues by ReferenceNbr.
2. **Pull the document with its lines**: `get_record(entity, id, expand="Details")`.
   Key formats: PurchaseOrder `Type/OrderNbr` (Type is usually `Normal`); PurchaseReceipt
   `Type/ReceiptNbr`; Bill `Type/ReferenceNbr` (Type `Bill`); SalesOrder `OrderType/OrderNbr`;
   TransferOrder / InventoryIssue single `ReferenceNbr`.
3. **Run the matching playbook below.**
4. **Report**: root cause in plain language -> the exact field(s) that prove it -> the proposed fix.
5. **Only write after the user confirms.** Show the before/after first.

## Playbook - PurchaseOrder will not receive  ⭐ most common

Header fields: `Type, OrderNbr, Status, Hold, VendorID, OrderTotal, LineTotal, ControlTotal, Date`.
Line (`Details`) fields: `LineNbr, InventoryID, OrderQty, QtyOnReceipts, Completed, Cancelled,
CompleteOn, MinReceiptPercent, MaxReceiptPercent, ReceiptAction, UnitCost, ExtendedCost, WarehouseID`.

Check, in order:
- **Line `Completed` = true** while `QtyOnReceipts < OrderQty` -> the line was auto/ manually completed
  and is closed to further receipt. **This is the classic "Status Completed, can't receive" case.**
  *Fix:* reopen the PO and set the line `Completed` back to `false` (uncheck Completed per line).
- **`CompleteOn` threshold reached** - when `QtyOnReceipts >= OrderQty * CompleteOn/100` the line
  auto-completes. If they need to receive past it, the line must be reopened.
- **Line `Cancelled` = true** -> line was cancelled; cannot receive. Needs re-add, not reopen.
- **Header `Status` = `Completed` / `Closed`** -> reopen the order before any line will accept receipt.
- **Header `Hold` = true** -> on hold; remove hold first.
- **`MaxReceiptPercent`** limits over-receipt; `ReceiptAction` shows the over-receipt policy.

Open qty per line = `OrderQty - QtyOnReceipts`. Report which line(s) block receiving.

*Proposed fix (preview, confirm before applying):*
```
upsert_record(entity="PurchaseOrder", entity_record={
  "Type": {"value":"Normal"}, "OrderNbr": {"value":"P0xxxxx"},
  "Details": [{"LineNbr": {"value": 1}, "Completed": {"value": false}}]
})
```
If the API rejects the line edit because the order is Completed/Closed, the order has to be reopened in
the UI first (PO301000 -> Actions) - say so plainly rather than forcing it.

## Playbook - total looks wrong (PO/SO detail-total mismatch)

The PO/SO header carries `OrderTotal` (and `LineTotal`, `ControlTotal`). A "Detail Total" shown in the
UI can be a **stored value that does not recalc when a unit cost is changed**, so it shows the original
price while `LineTotal`/`OrderTotal` are correct and are what actually drive the document.
- Recompute from lines: `sum(ExtendedCost)` should equal `LineTotal`.
- If `LineTotal`/`OrderTotal` reconcile to the lines but a "Detail Total" differs -> it is the stale
  stored display value, **not** a data error. State this; no write needed.
- If `ControlTotal` != `OrderTotal`, the document will not release until they match - surface the delta.

## Playbook - PurchaseReceipt will not convert to a Bill

Header: `Status, Hold, CreateBill, UnbilledQuantity, TotalQty, TotalCost, VendorID, Type`.
- **`Status` not `Released`** -> release the receipt first (`ReleasePurchaseReceipt`).
- **`UnbilledQuantity` = 0** -> already fully billed; nothing to convert.
- **`Hold` = true** -> remove hold.
- Otherwise create the bill via `invoke_action(entity="PurchaseReceipt", action="CreateAPBill", ...)`.

## Playbook - Bill stuck / will not release

Header: `Status, Hold, ApprovedForPayment, Balance, Amount, DueDate, Vendor, Type`.
Line (`Details`): `Amount, Qty, UnitCost, ExtendedCost, Account, POOrderNbr, POReceiptNbr`.
- `Status = Pending Approval` -> needs approval; not an error.
- `Hold = true` -> on hold.
- `Status = Balanced` but unreleased -> release with `ReleaseBill`.
- Amount != sum of line `Amount` -> line/tax imbalance; show the delta.

## Playbook - SalesOrder stuck

Header: `OrderType, OrderNbr, Status, Hold, Date`. Common blocks: `Status = Credit Hold`
(use `ReleaseFromCreditHoldSalesOrder`), `On Hold` (remove hold), `Back Order` (insufficient
allocation/stock). Expand `Details` to see line allocation.

## TransferOrder / InventoryIssue

Header: `Status, Hold, ReferenceNbr` (+ `FromWarehouseID`/`ToWarehouseID` for transfers). Usual block
is `Status` not `Released` or `Hold = true`; release with `ReleaseTransferOrder` /
`ReleaseInventoryIssue` after confirming.

## Output format

```
Document: [P0xxxxx](browser_url)  - PurchaseOrder, Status=Open, Hold=No
Problem:  Line 1 cannot receive.
Cause:    Line 1 Completed=true with QtyOnReceipts 2 of OrderQty 5 (3 still open).
Fix:      Reopen the PO and set Line 1 Completed=false. Preview of the change is below.
```

## Tips

- Never write without showing the before/after and getting a yes.
- Pull `expand="Details"` for any receive/total/match question - the answer is almost always at line level.
- For "who changed this / when", add `custom="Document.LastModifiedByID,Document.CreatedByID"` (see also a
  change-audit skill if present).
