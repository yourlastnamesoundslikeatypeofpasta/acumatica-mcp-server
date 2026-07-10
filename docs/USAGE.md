# Usage & Acumatica API rules

The tools are thin wrappers over Acumatica's contract-based REST API. A few rules
make queries reliable. Most failures come from guessing field names or key
formats — so the golden rule is: **call `describe_entity` first.**

## The workflow

1. `list_entities("order")` — find the entity you want.
2. `describe_entity("SalesOrder")` — get its exact `fields`, `key_fields`,
   `key_format`, `actions`, and `expand` collections.
3. `list_records` / `get_record` — query using only the names from step 2.

## Field names in `$select` and `$filter`

- Field names **must exactly match** the names returned by `describe_entity().fields`.
- A wrong field name causes an HTTP 500 `KeyNotFoundException`, not a friendly
  error. Never guess — always pull names from `describe_entity`.

## Key format for `get_record` / `delete_record`

Keys are the entity's key-field **values joined with `/`**, in key-field order.
`describe_entity` tells you the exact format per entity.

| Shape | Example entity | `key_format` | Example key |
|-------|----------------|--------------|-------------|
| Single value | `Customer` | `<CustomerID>` | `C000001` |
| Composite | `SalesOrder` | `<OrderType>/<OrderNbr>` | `SO/000123` |
| Composite | `Bill` | `<Type>/<ReferenceNbr>` | `Bill/000123` |

A record's GUID (the `id` field from a list response) also works as the key.

Watch out for key values that themselves contain a `/` (e.g. an `ItemClass` ID
like `A/C`) — use the record GUID instead.

## Date filters

Date/time fields require an OData `datetimeoffset` literal, including the timezone
offset:

```
Date gt datetimeoffset'2026-05-01T00:00:00-04:00'
```

Plain strings or `datetime''` literals are rejected.

## `$orderby` may be ignored

On many tenants `$orderby` is silently ignored and records come back in arbitrary
order. To reliably find the most recent records:

1. Filter to a **narrow date window first** (e.g. the last 14 days) and expand
   (30, then 90 days) only if empty.
2. Sort the results **client-side** by `Date` desc, then `LastModifiedDateTime` desc.

This resolves "last created" queries in 1–2 calls instead of scanning everything.

## Pulling extension fields with `$custom`

Standard fields not in the contract (and user-defined `UsrXxx` fields) can be
pulled with the `custom=` parameter, formatted as `ViewName.FieldName`
(comma-separated for several):

```
custom="Document.CreatedByID,Document.LastModifiedByID"
```

Call `get_schema(entity)` to discover the available view names and extension
fields for an entity.

## Query-only (inquiry) entities

Some entities are inquiry/summary views with **no addressable key**.
`describe_entity` marks them `query_only: true` and lists their
`mandatory_filters`. For these:

- **Do not** call `get_record`.
- Call `list_records` with **at least the mandatory filter fields** — calling with
  no filter typically returns HTTP 500 on these views.

## Browser deep links

`list_records` and `get_record` inject a `browser_url` into each record for
entities with a known screen mapping. Render the record's identifier as a
Markdown link using it, so a human can click straight through to the record in
Acumatica to verify:

```
[000123](https://your-instance.acumatica.com/Main?CompanyID=YourCompany&ScreenId=SO301000&OrderType=SO&OrderNbr=000123)
```

The screen mapping lives in `SCREEN_MAP` in `server.py`. If a link lands on the
wrong record, copy the correct URL from your browser and adjust the mapping (or
the per-type value maps like `_AP_DOCTYPE`) for your tenant.

## Writes are gated (read-only by default)

`upsert_record`, `delete_record`, and `invoke_action` change data in whatever
tenant you're connected to, so they are **disabled by default**. Enable them
deliberately via environment variables:

- `ACUMATICA_ALLOW_WRITES=1` — enables `upsert_record` and `invoke_action`
- `ACUMATICA_ALLOW_DELETES=1` — enables `delete_record`

When a mutating tool is called while disabled, it returns a `403`-style envelope
with a `hint` telling you which variable to set — no request is sent to Acumatica.

The request body for writes uses Acumatica's wrapped shape:

```json
{ "OrderType": { "value": "SO" }, "CustomerID": { "value": "C000001" } }
```

Test against a sandbox tenant or a read-only role before pointing at production.
