"""Acumatica MCP server.

Exposes a small set of generic tools that cover the full Acumatica contract-based
REST surface (any of the ~119 entities defined in the tenant's OpenAPI spec).

Tools:
    describe_entity   local  - fields, key format, actions, expand for any entity
    list_entities     local  - lists known entities from entity_catalog.json
    list_records      GET    /{Entity}                  with OData params
    get_record        GET    /{Entity}/{ids|id}         single record
    upsert_record     PUT    /{Entity}                  create or update
    delete_record     DELETE /{Entity}/{id}
    invoke_action     POST   /{Entity}/{ActionName}     entity actions (Release, Cancel, ...)
    get_schema        GET    /{Entity}/$adHocSchema     extension-field schema + view names
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------- setup ----------
# Which .env file to load is selectable via ACUMATICA_ENV_FILE so a single server.py
# can serve multiple Acumatica instances (e.g. production vs. a test/sandbox tenant).
# The value may be an absolute path or a name relative to this directory. Defaults to ".env".
#
# Provide credentials via EITHER your MCP client's env block (the process environment)
# OR this .env file - pick one, you do not need both. override=False means the process
# environment wins; the .env file only fills in variables that are not already set, so a
# stale .env cannot silently override the config passed from your MCP client.
_env_file = os.environ.get("ACUMATICA_ENV_FILE", ".env")
_env_path = Path(_env_file)
if not _env_path.is_absolute():
    _env_path = Path(__file__).parent / _env_path
load_dotenv(_env_path, override=False)
logging.basicConfig(level=os.environ.get("ACUMATICA_LOG_LEVEL", "INFO"))
log = logging.getLogger("acumatica-mcp")

BASE_URL = os.environ.get("ACUMATICA_BASE_URL", "").rstrip("/")
ENDPOINT_PATH = os.environ.get("ACUMATICA_ENDPOINT_PATH", "/entity/Default/24.200.001")
USERNAME = os.environ.get("ACUMATICA_USERNAME", "")
PASSWORD = os.environ.get("ACUMATICA_PASSWORD", "")
COMPANY = os.environ.get("ACUMATICA_COMPANY", "")
BRANCH = os.environ.get("ACUMATICA_BRANCH", "")
LOCALE = os.environ.get("ACUMATICA_LOCALE", "en-US")

# Write safety: this server is READ-ONLY by default. Mutating tools stay disabled
# unless explicitly enabled, so pointing it at a production tenant cannot create,
# update, or delete data by accident.
#   ACUMATICA_ALLOW_WRITES=1   enables upsert_record and invoke_action
#   ACUMATICA_ALLOW_DELETES=1  enables delete_record
_TRUTHY = {"1", "true", "yes", "on"}
ALLOW_WRITES = os.environ.get("ACUMATICA_ALLOW_WRITES", "").strip().lower() in _TRUTHY
ALLOW_DELETES = os.environ.get("ACUMATICA_ALLOW_DELETES", "").strip().lower() in _TRUTHY

if not BASE_URL or not USERNAME or not PASSWORD:
    print(
        "ERROR: ACUMATICA_BASE_URL, ACUMATICA_USERNAME, and ACUMATICA_PASSWORD must "
        "be set in the environment or .env file.",
        file=sys.stderr,
    )
    sys.exit(1)

CATALOG_PATH = Path(__file__).parent / "entity_catalog.json"
try:
    ENTITY_CATALOG: dict[str, dict[str, Any]] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    ENTITY_CATALOG = {}

# ---------- browser deep-link map ----------
# Acumatica URL format: {BASE_URL}/Main?CompanyID={co}&ScreenId={sid}&{key_params}
# params entries: (api_field_name, url_param_name, optional_value_map)
# value_map converts API display values -> URL internal codes (falls back to raw value).

_AP_DOCTYPE: dict[str, str] = {
    "Bill": "INV", "Credit Adj.": "APC", "Debit Adj.": "ADR",
    "Prepayment": "PPM", "Quick Check": "QCK", "Voided Quick Check": "VQC",
}
_AR_DOCTYPE: dict[str, str] = {
    "Invoice": "INV", "Credit Memo": "ARC", "Debit Memo": "ADR",
    "Finance Charge": "FC", "Cash Sale": "CSL", "Cash Return": "CSR",
}
_AR_PAYMENT: dict[str, str] = {
    "Payment": "PMT", "Prepayment": "PPM", "Credit Memo": "REF",
    "Voided Payment": "VPM",
}
_SO_DOCTYPE: dict[str, str] = {
    "Invoice": "INV", "Credit Memo": "CRM",
}
_PO_ORDERTYPE: dict[str, str] = {
    "Regular": "RO", "Blanket": "BO", "Drop Ship": "DP", "Project Drop Ship": "PD",
}
_PR_DOCTYPE: dict[str, str] = {
    "Receipt": "RT", "Return": "RN",
}

SCREEN_MAP: dict[str, dict[str, Any]] = {
    # ---- Accounts Payable ----
    "Bill":      {"screen_id": "AP301000", "params": [("Type", "DocType", _AP_DOCTYPE), ("ReferenceNbr", "RefNbr", None)]},
    "Check":     {"screen_id": "AP302000", "params": [("Type", "DocType", _AP_DOCTYPE), ("ReferenceNbr", "RefNbr", None)]},
    "Vendor":    {"screen_id": "AP303000", "params": [("VendorID", "VendorID", None)]},
    "VendorClass": {"screen_id": "AP201000", "params": [("ClassID", "VendorClassID", None)]},
    # ---- Accounts Receivable ----
    "Invoice":   {"screen_id": "AR301000", "params": [("Type", "DocType", _AR_DOCTYPE), ("ReferenceNbr", "RefNbr", None)]},
    "Payment":   {"screen_id": "AR302000", "params": [("Type", "DocType", _AR_PAYMENT), ("ReferenceNbr", "RefNbr", None)]},
    "Customer":  {"screen_id": "AR303000", "params": [("CustomerID", "CustomerID", None)]},
    "CustomerClass": {"screen_id": "AR201000", "params": [("ClassID", "CustomerClassID", None)]},
    "CustomerLocation": {"screen_id": "AR303000", "params": [("Customer", "CustomerID", None), ("LocationID", "LocationID", None)]},
    # ---- Sales ----
    "SalesOrder":   {"screen_id": "SO301000", "params": [("OrderType", "OrderType", None), ("OrderNbr", "OrderNbr", None)]},
    "Shipment":     {"screen_id": "SO302000", "params": [("ShipmentNbr", "ShipmentNbr", None)]},
    "SalesInvoice": {"screen_id": "SO303000", "params": [("Type", "DocType", _SO_DOCTYPE), ("ReferenceNbr", "RefNbr", None)]},
    # ---- Purchasing ----
    "PurchaseOrder":   {"screen_id": "PO301000", "params": [("Type", "OrderType", _PO_ORDERTYPE), ("OrderNbr", "OrderNbr", None)]},
    "PurchaseReceipt": {"screen_id": "PO302000", "params": [("Type", "ReceiptType", _PR_DOCTYPE), ("ReceiptNbr", "ReceiptNbr", None)]},
    # ---- CRM ----
    "Contact": {"screen_id": "CR302000", "params": [("ContactID", "ContactID", None)]},
    # ---- General Ledger ----
    "JournalTransaction": {"screen_id": "GL301000", "params": [("Module", "Module", None), ("BatchNbr", "BatchNbr", None)]},
    "Account":    {"screen_id": "GL202500", "params": [("AccountCD", "AccountCD", None)]},
    "Subaccount": {"screen_id": "GL203000", "params": [("SubaccountCD", "SubaccountCD", None)]},
    "Ledger":     {"screen_id": "GL201500", "params": [("LedgerID", "LedgerID", None)]},
    "FinancialPeriod": {"screen_id": "GL101000", "params": [("FinancialYear", "Year", None)]},
    # ---- Inventory ----
    "TransferOrder":       {"screen_id": "IN304000", "params": [("ReferenceNbr", "RefNbr", None)]},
    "InventoryReceipt":    {"screen_id": "IN301000", "params": [("ReferenceNbr", "RefNbr", None)]},
    "InventoryIssue":      {"screen_id": "IN302000", "params": [("ReferenceNbr", "RefNbr", None)]},
    "InventoryAdjustment": {"screen_id": "IN304500", "params": [("ReferenceNbr", "RefNbr", None)]},
    "StockItem":    {"screen_id": "IN202500", "params": [("InventoryID", "InventoryID", None)]},
    "NonStockItem": {"screen_id": "IN202000", "params": [("InventoryID", "InventoryID", None)]},
    "ItemClass":    {"screen_id": "IN201000", "params": [("ClassID", "ItemClassID", None)]},
    "ItemWarehouse":{"screen_id": "IN204500", "params": [("InventoryID", "InventoryID", None), ("WarehouseID", "SiteID", None)]},
    "Warehouse":    {"screen_id": "IN204000", "params": [("WarehouseID", "SiteID", None)]},
    "PhysicalInventoryReview": {"screen_id": "IN305000", "params": [("ReferenceNbr", "RefNbr", None)]},
    "KitAssembly":  {"screen_id": "IN307000", "params": [("ReferenceNbr", "RefNbr", None)]},
    # ---- Cash Management ----
    "PaymentMethod": {"screen_id": "CA204000", "params": [("PaymentMethodID", "PaymentMethodID", None)]},
}

mcp = FastMCP("acumatica")


# ---------- HTTP session (cookie-based) ----------
class AcumaticaAuthError(RuntimeError):
    """Raised when logging in to Acumatica fails (bad credentials, locked account)."""


class AcumaticaSession:
    """Thin wrapper that lazily logs in and keeps the session cookie alive.

    A lock guards login and lazy client creation so concurrent tool dispatch
    cannot double-login or race on the shared httpx client.
    """

    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._logged_in: bool = False
        self._lock = threading.Lock()

    def login(self) -> None:
        if self._logged_in:
            return
        with self._lock:
            if self._logged_in:  # re-check under the lock
                return
            if self._client is None:
                self._client = httpx.Client(
                    base_url=BASE_URL,
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    follow_redirects=False,
                )
            body: dict[str, Any] = {
                "name": USERNAME,
                "password": PASSWORD,
                "locale": LOCALE,
            }
            if COMPANY:
                body["company"] = COMPANY
            if BRANCH:
                body["branch"] = BRANCH
            log.info("Logging in to Acumatica as %s", USERNAME)
            r = self._client.post("/entity/auth/login", json=body)
            if r.status_code >= 400:
                raise AcumaticaAuthError(
                    f"Acumatica login failed ({r.status_code}): {r.text[:300]}"
                )
            self._logged_in = True

    def logout(self) -> None:
        with self._lock:
            if not self._logged_in or self._client is None:
                return
            try:
                self._client.post("/entity/auth/logout")
            except Exception:
                pass
            self._logged_in = False

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: Any | None = None,
    ) -> httpx.Response:
        self.login()
        c = self._client
        assert c is not None  # login() guarantees the client exists
        full_path = f"{ENDPOINT_PATH}{path}"
        log.debug("%s %s params=%s", method, full_path, params)
        r = c.request(method, full_path, params=params, json=json_body)
        # Re-auth on 401, retry once
        if r.status_code == 401:
            log.info("Session expired, re-logging in")
            with self._lock:
                self._logged_in = False
            self.login()
            r = c.request(method, full_path, params=params, json=json_body)
        return r


session = AcumaticaSession()


def _build_browser_url(entity: str, record: dict) -> str | None:
    """Construct a direct browser deep-link using the record's key field values.

    Format: {BASE_URL}/Main?CompanyID={co}&ScreenId={sid}&{key_params}
    Returns None if the entity has no SCREEN_MAP entry or a key field is missing.
    Value maps translate API display values to Acumatica URL internal codes
    (e.g. Type="Bill" -> DocType=INV). Falls back to raw value when not in map.
    """
    entry = SCREEN_MAP.get(entity)
    if not entry:
        return None

    url_params: dict[str, str] = {}
    if COMPANY:
        url_params["CompanyID"] = COMPANY
    url_params["ScreenId"] = entry["screen_id"]

    for api_field, url_param, value_map in entry["params"]:
        raw = record.get(api_field, {})
        val = raw.get("value") if isinstance(raw, dict) else raw
        if val is None:
            return None  # key field absent - cannot build URL
        val = str(val)
        if value_map:
            val = value_map.get(val, val)  # prefer internal code; fall back to display value
        url_params[url_param] = val

    return f"{BASE_URL}/Main?{urlencode(url_params)}"


def _error_hint(status: int, text: str, entity: str | None) -> str | None:
    """Translate common raw Acumatica errors into an actionable next step."""
    ename = entity or "<entity>"
    if "KeyNotFoundException" in text:
        return (
            f"A field name in $filter/$select does not exist on {ename}. "
            f"Call describe_entity('{ename}') and use only the exact names it returns."
        )
    if "CannotOptimizeException" in text or "Optimization cannot be performed" in text:
        meta = ENTITY_CATALOG.get(entity or "", {})
        mf = meta.get("mandatory_filters")
        if mf:
            return (
                f"{ename} is an inquiry view that requires mandatory filter fields: "
                f"{', '.join(mf)}. Add them to filter= and retry."
            )
        return (
            f"{ename} could not run this query - inquiry views need at least one "
            f"mandatory filter field. Call describe_entity('{ename}') for details; "
            f"some inquiries (SalesPricesInquiry, VendorPricesInquiry) are non-functional via REST."
        )
    if "Type conversions are not supported" in text or "type conversion" in text.lower():
        return (
            "A numeric/decimal literal in $filter was rejected (e.g. 'gt 0'). "
            "Drop that clause and filter client-side instead."
        )
    if status == 403:
        return (
            f"The API service account has no rights to {ename} on this tenant "
            f"(CRM, Field Service, Payroll and some inquiries are permission-locked). "
            f"An Acumatica admin must grant access."
        )
    return None


def _format_response(r: httpx.Response, entity: str | None = None) -> dict[str, Any]:
    """Uniform response envelope. Injects browser_url into records when entity is known."""
    out: dict[str, Any] = {
        "status": r.status_code,
        "ok": r.is_success,
    }
    if not r.is_success:
        # raw upstream error text (truncated); handy for debugging your own tenant
        out["error"] = r.text[:1000]
        hint = _error_hint(r.status_code, r.text, entity)
        if hint:
            out["hint"] = hint
        return out
    try:
        body = r.json()
    except ValueError:
        out["data"] = r.text
        return out

    # Inject browser_url into every record that has a GUID and a known screen ID
    if entity:
        if isinstance(body, list):
            for rec in body:
                if isinstance(rec, dict):
                    url = _build_browser_url(entity, rec)
                    if url:
                        rec["browser_url"] = url
        elif isinstance(body, dict):
            url = _build_browser_url(entity, body)
            if url:
                body["browser_url"] = url

    out["data"] = body
    return out


def _build_odata_params(
    filter_: str | None,
    top: int | None,
    skip: int | None,
    select: str | None,
    expand: str | None,
    orderby: str | None,
    custom: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if filter_:
        params["$filter"] = filter_
    if top is not None:
        params["$top"] = top
    if skip is not None:
        params["$skip"] = skip
    if select:
        params["$select"] = select
    if expand:
        params["$expand"] = expand
    if orderby:
        params["$orderby"] = orderby
    if custom:
        params["$custom"] = custom
    return params


def _enc_key(key: str) -> str:
    """URL-encode a record key for use in the path, preserving '/' as the
    composite-key separator. Encodes '#', '?', '%', spaces, etc. so a key like a
    StockItem's "#05773" is not mangled - an unencoded '#' would be parsed as a URL
    fragment and silently drop the key from the request.
    """
    return "/".join(quote(segment, safe="") for segment in key.split("/"))


def _write_blocked(kind: str, env_var: str) -> dict[str, Any]:
    """Uniform 'mutations disabled' response for the read-only-by-default gate."""
    return {
        "status": 403,
        "ok": False,
        "error": f"{kind} are disabled - this Acumatica MCP server is read-only by default.",
        "hint": f"Set {env_var}=1 in the server's environment and restart to enable {kind.lower()}.",
    }


def _request(
    method: str,
    path: str,
    *,
    entity: str | None = None,
    params: dict | None = None,
    json_body: Any | None = None,
) -> dict[str, Any]:
    """Run a request through the session and format it, turning connection and
    auth failures into the uniform error envelope instead of raising out of a tool.
    """
    try:
        r = session.request(method, path, params=params, json_body=json_body)
    except AcumaticaAuthError as e:
        return {"status": 401, "ok": False, "error": str(e)}
    except httpx.HTTPError as e:
        return {"status": 0, "ok": False, "error": f"Could not reach Acumatica: {e}"}
    return _format_response(r, entity=entity)


def _maybe_key_hint(out: dict[str, Any], entity: str) -> dict[str, Any]:
    """Add a key-format hint to a failed single-record request (likely a bad key)."""
    if not out.get("ok") and out.get("status") in (400, 404, 500):
        meta = ENTITY_CATALOG.get(entity, {})
        kf = meta.get("key_format")
        if kf and "hint" not in out:
            out["hint"] = (
                f"Check the key: {entity} key_format is \"{kf}\" "
                f"(key field values joined with '/'). A record GUID also works."
            )
    return out


# ---------- tools ----------
@mcp.tool()
def list_entities(filter: str | None = None) -> dict[str, Any]:
    """List the Acumatica entities exposed by this tenant's OpenAPI spec.

    Args:
        filter: Optional case-insensitive substring to narrow the list,
                e.g. "order" returns SalesOrder, PurchaseOrder, etc.

    Returns:
        {count, entities: [{name, actions}]}

    Tip: call describe_entity(name) to get the full field list, key format,
         and expandable sub-collections for any entity before querying it.
    """
    items = []
    for name, meta in sorted(ENTITY_CATALOG.items()):
        if filter and filter.lower() not in name.lower():
            continue
        items.append({"name": name, "actions": meta.get("actions", [])})
    return {"count": len(items), "entities": items}


@mcp.tool()
def describe_entity(entity: str) -> dict[str, Any]:
    """Return the full metadata for an entity: fields, key format, actions, and sub-collections.

    Always call this before list_records or get_record when you are unsure of:
      - which field names are valid (use these in select= and filter= expressions)
      - how to format the id= argument for get_record / delete_record
      - which sub-collections can be passed to expand=

    Args:
        entity: Entity name, e.g. "SalesOrder", "Bill", "Customer".

    Returns one of two shapes:

    Normal entity (has a key):
        {
          "entity": "SalesOrder",
          "fields": ["OrderType", "OrderNbr", "CustomerID", ...],   # valid $select / $filter names
          "key_fields": ["OrderType", "OrderNbr"],                   # fields that make up the URL key
          "key_format": "Slash-separated: <OrderType>/<OrderNbr>",  # how to build the id= string
          "actions": ["CancelSalesOrder", ...],
          "expand": ["Details", "Shipments", ...]
        }

    Query-only entity (inquiry/summary view - no addressable key):
        {
          "entity": "AccountSummaryInquiry",
          "query_only": true,
          "note": "This entity has no addressable key - use list_records with filter= only. ...",
          "fields": [...],
          "actions": [],
          "expand": []
        }
        For query-only entities: do NOT call get_record - pass at least one filter= to list_records.
        Calling list_records with no filter on these entities returns HTTP 500 on this tenant.

    Returns {error: "Unknown entity"} if the entity is not in the catalog.

    Examples:
        describe_entity("SalesOrder")
        # -> key_format: "Slash-separated: <OrderType>/<OrderNbr>"
        # -> use get_record("SalesOrder", "QT/I004264")

        describe_entity("Bill")
        # -> key_format: "Slash-separated: <Type>/<ReferenceNbr>"
        # -> use get_record("Bill", "Bill/012979")

        describe_entity("AccountSummaryInquiry")
        # -> query_only: true
        # -> use list_records("AccountSummaryInquiry", filter="Period eq '202506'")
    """
    meta = ENTITY_CATALOG.get(entity)
    if meta is None:
        return {"error": f"Unknown entity '{entity}'. Call list_entities() to see available names."}

    result: dict[str, Any] = {
        "entity": entity,
        "fields": meta.get("fields", []),
        "actions": meta.get("actions", []),
        "expand": meta.get("expand", []),
    }

    if meta.get("query_only"):
        result["query_only"] = True
        mf = meta.get("mandatory_filters")
        if mf:
            result["mandatory_filters"] = mf
            result["note"] = (
                "This entity has no addressable key - use list_records with filter= only "
                f"(get_record will fail). Mandatory filter fields: {', '.join(mf)} - "
                "omitting them returns HTTP 500 on this tenant."
            )
        else:
            result["note"] = (
                "This entity has no addressable key - use list_records with filter= only. "
                "Calling get_record on it will fail. "
                "Calling list_records with no filter may return a 500 error on this tenant "
                "(inquiry views require at least one mandatory filter field)."
            )
    else:
        result["key_fields"] = meta.get("key_fields", [])
        result["key_format"] = meta.get("key_format", "Unknown - check get_schema() or Acumatica docs")

    # Live-validated usage note (performance, permissions, known breakage), if any
    if meta.get("note"):
        result["usage_note"] = meta["note"]

    return result


@mcp.tool()
def list_records(
    entity: str,
    filter: str | None = None,
    top: int | None = 50,
    skip: int | None = None,
    select: str | None = None,
    expand: str | None = None,
    orderby: str | None = None,
    custom: str | None = None,
) -> dict[str, Any]:
    """Retrieve records from an Acumatica entity using OData query parameters.

    IMPORTANT: Call describe_entity(entity) first to get the exact field names for
    this entity. Using a field name that doesn't exist causes a hard 500 error.

    Args:
        entity: Entity name, e.g. "SalesOrder", "Bill", "Customer".
        filter: OData $filter expression, e.g. "Status eq 'Open'".
                Only use field names returned by describe_entity() - guessed names
                cause KeyNotFoundException (500).
                Date/time fields require datetimeoffset literal format:
                    Date gt datetimeoffset'2026-05-01T00:00:00-04:00'
                Plain strings or datetime'' literals will fail.
        top:    Max rows to return. Defaults to 50; use a smaller number when exploring.
        skip:   Rows to skip (pagination).
        select: Comma-separated fields, e.g. "OrderNbr,CustomerID,OrderTotal".
                Only use field names returned by describe_entity() - invalid names -> 500.
        expand: Comma-separated sub-collections to inline, e.g. "Details,Shipments".
                Valid values are listed in describe_entity() under 'expand'.
        orderby: e.g. "Date desc".
                 NOTE: $orderby is silently ignored by this tenant.
                 To get the most recent records, use a date filter instead and sort
                 client-side. Use a narrow window first - expand only if empty:
                   Step 1: filter="Date gt datetimeoffset'<today-14d>T00:00:00-04:00'"
                   Step 2: if empty, retry with today-30d, then today-90d
                 Client-side: sort results by Date desc, then LastModifiedDateTime desc
                 to find the single most-recent record.
                 This resolves "last created" queries in 1-2 API calls instead of 5+.
        custom: Pull user-defined (DAC extension) fields not in the standard contract.
                Format: "ViewName.FieldName" - multiple fields comma-separated.
                Example: "Document.LastModifiedByID,Document.CreatedByID"
                To discover available view names and fields, call get_schema(entity).
                Common view name for header-level fields: "Document".

    Returns:
        {status, ok, data: [records]} or {status, ok: false, error}
        Each record includes a `browser_url` field (for entities with a known screen ID)
        linking directly to that record in the Acumatica web UI.
        ALWAYS render the primary identifier (ReferenceNbr, OrderNbr, etc.) as a
        Markdown hyperlink using browser_url so the user can click through to audit:
            [050297](https://example.acumatica.com/Main?ScreenId=AP301000&ID=...)
    """
    params = _build_odata_params(filter, top, skip, select, expand, orderby, custom)
    return _request("GET", f"/{entity}", entity=entity, params=params)


@mcp.tool()
def get_record(
    entity: str,
    id: str,
    select: str | None = None,
    expand: str | None = None,
    custom: str | None = None,
) -> dict[str, Any]:
    """Get a single record by its key.

    Call describe_entity(entity) first to find the key_fields and key_format
    for this entity - the key format varies per entity and using the wrong
    format causes a 500 error.

    Args:
        entity: Entity name.
        id:     The record key - key field values joined with '/' in key order.
                Examples:
                  SalesOrder  -> "QT/I004264"    (OrderType/OrderNbr)
                  Bill        -> "Bill/001234"   (Type/ReferenceNbr)
                  Customer    -> "C000001"       (CustomerID only)
                  Invoice     -> "INV/001234"    (Type/ReferenceNbr)
                Use describe_entity() to find the exact key_fields for any entity.
                A GUID (the session 'id' field) also works if you have it.
        select / expand / custom: same as list_records.

    Returns:
        {status, ok, data: {record}} or {status, ok: false, error}
        Record includes a `browser_url` field (for entities with a known screen ID).
        Render the record identifier as a Markdown hyperlink using browser_url:
            [050297](https://example.acumatica.com/Main?ScreenId=AP301000&ID=...)
    """
    params = _build_odata_params(None, None, None, select, expand, None, custom)
    out = _request("GET", f"/{entity}/{_enc_key(id)}", entity=entity, params=params)
    return _maybe_key_hint(out, entity)


@mcp.tool()
def upsert_record(entity: str, data: dict[str, Any]) -> dict[str, Any]:
    """Create or update a record (PUT /{Entity}). Requires ACUMATICA_ALLOW_WRITES=1.

    Acumatica's contract-based API uses PUT for both create and update - the
    server decides based on whether key fields match an existing record.

    Args:
        entity: Entity name.
        data:   Body in Acumatica's `{"FieldName": {"value": ...}}` shape.
                Example:
                    {"OrderType": {"value": "SO"},
                     "CustomerID": {"value": "ABARTENDE"},
                     "Details": [{"InventoryID": {"value": "AALEGO500"}, "Quantity": {"value": 5}}]}
    """
    if not ALLOW_WRITES:
        return _write_blocked("Writes", "ACUMATICA_ALLOW_WRITES")
    return _request("PUT", f"/{entity}", entity=entity, json_body=data)


@mcp.tool()
def delete_record(entity: str, id: str) -> dict[str, Any]:
    """Delete a record by ID or key fields. Requires ACUMATICA_ALLOW_DELETES=1."""
    if not ALLOW_DELETES:
        return _write_blocked("Deletes", "ACUMATICA_ALLOW_DELETES")
    out = _request("DELETE", f"/{entity}/{_enc_key(id)}", entity=entity)
    return _maybe_key_hint(out, entity)


@mcp.tool()
def invoke_action(
    entity: str,
    action: str,
    entity_record: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke a named action on an entity (POST /{Entity}/{Action}). Requires ACUMATICA_ALLOW_WRITES=1.

    Examples:
        invoke_action("SalesOrder", "CancelSalesOrder",
                      entity_record={"OrderType": {"value": "SO"},
                                     "OrderNbr": {"value": "SO012345"}})
        invoke_action("Bill", "ReleaseBill",
                      entity_record={"ReferenceNbr": {"value": "001234"}})

    Args:
        entity: Entity name.
        action: Action name (call describe_entity(entity) to see available actions).
        entity_record: The record the action runs against, in Acumatica's wrapped format.
        parameters: Action-specific parameters, if any.

    Returns 204 No Content for fire-and-forget actions; 202 for long-running.
    """
    if not ALLOW_WRITES:
        return _write_blocked("Actions", "ACUMATICA_ALLOW_WRITES")
    body: dict[str, Any] = {}
    if entity_record is not None:
        body["entity"] = entity_record
    if parameters is not None:
        body["parameters"] = parameters
    return _request("POST", f"/{entity}/{action}", json_body=body or None)


@mcp.tool()
def get_schema(entity: str) -> dict[str, Any]:
    """Return the entity's extension-field schema (GET /{Entity}/$adHocSchema).

    Two reasons to call this:

    1. Discover user-defined extension fields (UsrXxx fields, attribute fields like
       AttributeCOLOR) that are NOT in the standard contract and not listed by
       describe_entity(). These appear nested under a view name in the response.

    2. Discover the available VIEW NAMES for this entity's graph. View names are the
       first part of the 'ViewName.FieldName' string needed by the custom= parameter.
       Standard Acumatica DAC fields (e.g. CreatedByID, LastModifiedByID) that are
       absent from the contract can ALSO be pulled via custom= using these view names,
       even though they don't appear explicitly in this schema response.

    Common SalesOrder view names (confirmed working):
        Document          - SOOrder header fields (CreatedByID, BranchID, etc.)
        CurrentDocument   - additional header computed fields
        Transactions      - line-level fields (on Details rows)
        Adjustments       - payment application fields

    Example workflow:
        1. get_schema("SalesOrder")               # identify view names
        2. list_records("SalesOrder",             # pull extension + standard DAC fields
               custom="Document.CreatedByID,Document.UsrYourCustomField")
    """
    return _request("GET", f"/{entity}/$adHocSchema")


# ---------- entrypoint ----------
def main() -> None:
    import atexit

    atexit.register(session.logout)
    mcp.run()


if __name__ == "__main__":
    main()
