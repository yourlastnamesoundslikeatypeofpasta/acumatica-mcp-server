---
name: recent-records
description: Find the latest or most recent record(s) of any Acumatica entity - sales orders, bills, purchase orders, invoices, payments, shipments, transfer orders, inventory issues, items, customers, vendors, and more. Use this whenever the user asks for the most recent, latest, last, newest, or just-entered record, or says things like what is the latest sales order, show me recent POs, the last bill we entered, or the most recently modified customer - even when they never say the word recent. Many Acumatica tenants silently ignore OData $orderby, so a plain list cannot sort by date and returns arbitrary old rows. This skill applies the reliable date-window pattern (start narrow, widen if empty, sort client-side) so the user gets the truly most recent records instead of whatever the server happened to return.
---

# Recent Records

Answer "what's the latest / most recent / last X" questions for any Acumatica entity, reliably.

## Why this skill exists

Many Acumatica tenants **silently ignore `$orderby`** - passing `orderby="Date desc"` does nothing, and
`list_records` returns rows in arbitrary (effectively oldest-first) order. So the obvious approach -
"list the entity, sort by date, take the top one" - quietly returns the *wrong* record. Users rarely
notice, which makes it worse: they get a confidently-presented sales order from 2021 when they asked
for the newest one.

The fix is to **filter to a recent date window, then sort the small result client-side.** The API
honors `$filter` perfectly even though it ignores `$orderby`. This skill encodes that pattern plus the
two things people get wrong around it: which date field to use, and the exact datetime literal format.

## The procedure

### 1. Pin down the entity and the date field

Map the user's words to an entity (e.g. "PO" -> `PurchaseOrder`, "bill" -> `Bill`, "the latest order"
in a sales context -> `SalesOrder`). Then call **`describe_entity(entity)`** to get the real field
names - a guessed field name causes a hard HTTP 500, so never assume them.

Pick the date field from the user's *intent*, not just availability:

| The user means... | Words that signal it | Use this field |
|---|---|---|
| Most recently **created / entered / placed / dated** | "latest", "last one we entered", "newest", "just came in" | the document date - usually `Date` (some entities use `OrderDate`, `DocDate`) |
| Most recently **changed / touched / updated** | "recently modified", "last updated", "changed since..." | `LastModifiedDateTime` |

When it's genuinely ambiguous, default to the **document date** (`Date`) and break ties with
`LastModifiedDateTime` - that matches what people usually mean by "the latest one." If the document
date field isn't obvious from `describe_entity`, `LastModifiedDateTime` is a safe fallback that exists
on virtually every entity.

> **Subtlety worth knowing:** Acumatica's contract `Date` is the *document* date (the date on the
> order/bill), **not** the row's creation timestamp. They usually agree, but a backdated document can
> differ. True row-creation time isn't in the standard contract - if the user explicitly needs
> *entry/creation* order, pull it via `$custom` (e.g. `custom="Document.CreatedByID"` discovers the
> view; creation timestamp fields live under the same view). For 95% of "what's the latest" questions,
> document `Date` desc is exactly right - don't over-reach for the custom path unless asked.

### 2. Build a narrow date window from today

Take today's date (it's in your environment context) and start with a **14-day** window. Format the
filter with a `datetimeoffset` literal - plain strings or `datetime''` literals fail:

```
<DateField> gt datetimeoffset'2026-06-16T00:00:00-04:00'
```

- The `-04:00` is an example UTC offset (US Eastern). It's `-04:00` during daylight time (roughly March-November)
  and `-05:00` otherwise. Because the window is day-granular, an hour of slop at the boundary never
  changes the answer - don't agonize over it.
- Replace `2026-06-16` with `today - 14 days`.

### 3. Query, keeping the payload small

```
list_records(entity,
             filter="<DateField> gt datetimeoffset'<today-14d>T00:00:00-04:00'",
             select="<key fields>,<DateField>,LastModifiedDateTime",
             top=50)
```

Always pass `select=` with just the fields you need. A few entities (`StockItem`, `ItemWarehouse`)
return enormous rows without it.

### 4. Widen only if empty

If the window came back empty, the entity is just sparse - widen and retry, stopping at the first
window that returns rows:

**14 days -> 30 -> 90 -> 365.** This resolves almost every case in one or two calls. Never fall back to
an *unfiltered* `list_records` to "just get everything and sort" - that returns arbitrary old data,
which is the exact failure this skill exists to prevent.

### 5. Sort client-side and return what was asked

Sort the returned rows by your chosen date **descending**, tie-breaking on `LastModifiedDateTime`
descending. Then return the count the user wanted:

- "the latest / the last / most recent X" -> **1** record
- "recent X", "the last few", "what's been coming in" -> **5-10** records

### 6. Link every record

Each record carries a `browser_url`. Render the identifier as a Markdown link so the user can click
straight to it in Acumatica to verify:

```
[SO012345](https://example.acumatica.com/Main?ScreenId=SO301000&...)
```

## Safety

This skill is read-only - it only queries, never writes - so it is safe to run against any tenant.

## Worked example

**User:** "what's the latest sales order we got?"

1. Entity = `SalesOrder`; `describe_entity("SalesOrder")` -> date field `Date` exists. Intent is
   "latest we got" -> document date.
2. Today is 2026-06-30 -> window start 2026-06-16.
3. `list_records("SalesOrder", filter="Date gt datetimeoffset'2026-06-16T00:00:00-04:00'",
   select="OrderType,OrderNbr,CustomerID,Date,OrderTotal,LastModifiedDateTime", top=50)`
4. Rows returned -> no need to widen.
5. Sort by `Date` desc, tie-break `LastModifiedDateTime` desc -> take the first.
6. Reply with the order rendered as a `browser_url` link, plus customer and total.

**Result:** the genuinely newest order, in one API call - versus a plain list that would have returned
an arbitrary historical order with no error to warn you.

## Gotchas

- **`$orderby` is a no-op here** - never trust server-side ordering for *any* entity.
- **Field names must come from `describe_entity`** - a wrong name is a 500, not an empty result.
- **Query-only entities** (inquiry views) need mandatory filters; `describe_entity` flags them with
  `query_only: true`. The windowing idea still applies, but combine it with their required filters.
- **Don't start unfiltered.** An unfiltered `list_records` to find "the latest" is the anti-pattern
  this skill replaces.
