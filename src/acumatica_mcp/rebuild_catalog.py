#!/usr/bin/env python3
"""Rebuild entity_catalog.json from the tenant's OpenAPI spec.

Usage:
    python rebuild_catalog.py <path_to_openapi_spec.json>

What it does:
  - Adds 'fields' (all scalar/object fields usable in $select / $filter)
  - Adds 'expand' (array sub-collections, if not already set)
  - Adds 'key_fields' + 'key_format' for get_record / delete_record id strings
  - Preserves existing 'actions' entries

Re-run whenever you get a new tenant OpenAPI spec.
"""
import json
import sys
from pathlib import Path

# Known composite keys for common entities (field order matches URL segment order).
# Key URL format: field values joined with '/', e.g. "<OrderType>/<OrderNbr>"
# These reflect the standard Acumatica 24.200.001 contract; verify against your
# own tenant if it has customizations.
KEY_OVERRIDES: dict[str, list[str]] = {
    "Account": ["AccountCD"],
    "AccountGroup": ["GroupID"],
    "Appointment": ["SrvOrdType", "RefNbr"],
    "ARPayment": ["Type", "ReferenceNbr"],
    "Bill": ["Type", "ReferenceNbr"],
    "BusinessAccount": ["BusinessAccountID"],
    "Case": ["CaseCD"],
    "Check": ["Type", "ReferenceNbr"],
    "Contact": ["ContactID"],
    "Customer": ["CustomerID"],
    "CustomerClass": ["ClassID"],
    "CustomerLocation": ["Customer", "LocationID"],
    "CustomerPaymentMethod": ["CustomerID", "CardAccountNbr"],
    "Employee": ["EmployeeID"],
    "FinancialPeriod": ["FinancialYear"],
    "InventoryAdjustment": ["ReferenceNbr"],
    "InventoryIssue": ["ReferenceNbr"],
    "InventoryItem": ["InventoryID"],
    "InventoryReceipt": ["ReferenceNbr"],
    "Invoice": ["Type", "ReferenceNbr"],
    "ItemClass": ["ClassID"],
    "ItemWarehouse": ["InventoryID", "WarehouseID"],
    "JournalTransaction": ["Module", "BatchNbr"],
    "JournalEntry": ["Module", "BatchNbr"],
    "Lead": ["LeadID"],
    "Ledger": ["LedgerID"],
    "NonStockItem": ["InventoryID"],
    "Opportunity": ["OpportunityID"],
    "Payment": ["Type", "ReferenceNbr"],
    "PhysicalInventoryReview": ["ReferenceNbr"],
    "PurchaseOrder": ["Type", "OrderNbr"],
    "PurchaseReceipt": ["Type", "ReceiptNbr"],
    "SalesInvoice": ["Type", "ReferenceNbr"],
    "SalesOrder": ["OrderType", "OrderNbr"],
    "ServiceOrder": ["SrvOrdType", "RefNbr"],
    "Shipment": ["ShipmentNbr"],
    "StockItem": ["InventoryID"],
    "Subaccount": ["SubaccountCD"],
    "Tax": ["TaxID"],
    "TaxCategory": ["TaxCategoryID"],
    "TransferOrder": ["ReferenceNbr"],
    "Vendor": ["VendorID"],
    "VendorClass": ["ClassID"],
    "Warehouse": ["WarehouseID"],
    "WorkOrder": ["OrderType", "OrderNbr"],
}

# Entities that CANNOT be listed without mandatory filter parameters.
# These are inquiry/summary views with BQL delegates — list_records with no filter
# returns HTTP 500 "Optimization cannot be performed."
# For these, query_only=true is set in the catalog; no key_format is generated.
# InventoryQuantityAvailable may also be permission-locked (403), depending on the tenant.
QUERY_ONLY_ENTITIES: set[str] = {
    "AccountDetailsForPeriodInquiry",
    "AccountSummaryInquiry",
    "Budget",
    "InventoryAllocationInquiry",
    "InventoryQuantityAvailable",
    "InventorySummaryInquiry",
    "PhysicalInventoryCount",
    "SalesPricesInquiry",
    "VendorPricesInquiry",
}

# Per-entity usage notes surfaced by describe_entity(). Generic guidance that
# prevent common failures (huge payloads, non-functional inquiries, permission gaps).
ENTITY_NOTES: dict[str, str] = {
    "StockItem": "ALWAYS pass select= with specific fields - the full record is enormous and can blow the response limit.",
    "ItemWarehouse": "ALWAYS pass select= with specific fields - full records are very large.",
    "ItemClass": "ClassIDs containing '/' (e.g. 'A/C') cannot be used as the get_record key - use the record GUID instead.",
    "JournalTransaction": "$select is not honored on this entity - pull records and pick fields client-side.",
    "SalesPricesInquiry": "Often NON-FUNCTIONAL via REST - queries (even filtered) tend to return 500 CannotOptimizeException. For pricing read StockItem/NonStockItem fields (DefaultPrice, MSRP, LastCost, AverageCost) instead.",
    "VendorPricesInquiry": "Often NON-FUNCTIONAL via REST - queries (even filtered) tend to return 500 CannotOptimizeException. For pricing read StockItem/NonStockItem fields (DefaultPrice, MSRP, LastCost, AverageCost) instead.",
    "InventoryQuantityAvailable": "May return 403 Forbidden if the API service account lacks access. Use InventorySummaryInquiry or ItemWarehouse instead.",
    "BusinessAccount": "May return 403 Forbidden if the CRM module is not granted to the API service account.",
    "Lead": "May return 403 Forbidden if the CRM module is not granted to the API service account.",
    "Opportunity": "May return 403 Forbidden if the CRM module is not granted to the API service account.",
    "Case": "May return 403 Forbidden if the CRM module is not granted to the API service account.",
    "Employee": "May return 403 Forbidden if the Payroll module is not granted to the API service account.",
    "ServiceOrder": "May return 403 Forbidden if the Field Service module is not granted to the API service account.",
    "Appointment": "May return 403 Forbidden if the Field Service module is not granted to the API service account.",
}

# Minimum filter fields required for query-only inquiry views.
# list_records on these entities without at least these filter fields returns HTTP 500.
MANDATORY_FILTERS: dict[str, list[str]] = {
    "AccountSummaryInquiry": ["Period", "Ledger", "Branch", "Subaccount"],
    "AccountDetailsForPeriodInquiry": ["FromPeriod", "ToPeriod", "Ledger"],
    "Budget": ["FinancialYear", "Ledger", "Branch"],
    "InventoryAllocationInquiry": ["InventoryID"],
    "InventorySummaryInquiry": ["InventoryID"],
    "PhysicalInventoryCount": ["InventoryID", "Location", "LotSerialNbr"],
}


def apply_annotations(catalog: dict) -> int:
    """Stamp ENTITY_NOTES and MANDATORY_FILTERS onto catalog entries. Returns count touched."""
    touched = 0
    for name, note in ENTITY_NOTES.items():
        if name in catalog:
            catalog[name]["note"] = note
            touched += 1
    for name, mf in MANDATORY_FILTERS.items():
        if name in catalog:
            catalog[name]["mandatory_filters"] = mf
            touched += 1
    return touched


# Base fields that appear on every entity via the Entity schema — omit from fields list
BASE_FIELDS = {"id", "rowNumber", "note", "custom", "error", "files"}

# Primitive value wrapper schemas — these hold a single .value; treat as scalar
VALUE_SCHEMAS = {
    "StringValue", "BooleanValue", "DecimalValue", "DateTimeValue",
    "IntValue", "GuidValue", "ByteArrayValue", "ShortValue", "LongValue",
    "DoubleValue", "FloatValue", "CustomField",
}


def resolve_ref(ref: str, schemas: dict) -> dict:
    name = ref.split("/")[-1]
    return schemas.get(name, {})


def extract_fields_and_expand(
    schema: dict, schemas: dict
) -> tuple[list[str], list[str]]:
    """Return (scalar_fields, array_fields) from a schema, resolving allOf."""
    scalars: list[str] = []
    arrays: list[str] = []

    if "$ref" in schema:
        return extract_fields_and_expand(resolve_ref(schema["$ref"], schemas), schemas)

    for sub in schema.get("allOf", []):
        s, a = extract_fields_and_expand(sub, schemas)
        scalars.extend(s)
        arrays.extend(a)

    for prop_name, prop_schema in schema.get("properties", {}).items():
        if prop_name in BASE_FIELDS:
            continue
        if prop_schema.get("type") == "array":
            arrays.append(prop_name)
        else:
            scalars.append(prop_name)

    return scalars, arrays


def main() -> None:
    catalog_path = Path(__file__).parent / "entity_catalog.json"

    if len(sys.argv) >= 2 and sys.argv[1] == "--annotate-only":
        # No spec needed: only (re)apply notes + mandatory_filters to the existing catalog.
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        touched = apply_annotations(catalog)
        catalog_path.write_text(
            json.dumps(dict(sorted(catalog.items())), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Annotated {catalog_path} ({touched} annotations applied)")
        return

    if len(sys.argv) < 2:
        print("Usage: rebuild_catalog.py <openapi_spec.json> | --annotate-only", file=sys.stderr)
        sys.exit(1)

    spec_path = Path(sys.argv[1])

    spec: dict = json.loads(spec_path.read_text(encoding="utf-8"))
    catalog: dict = json.loads(catalog_path.read_text(encoding="utf-8"))

    schemas = spec.get("components", {}).get("schemas", {})

    updated = 0
    for entity_name, entity_data in catalog.items():
        if entity_name not in schemas:
            continue

        fields, expand_cols = extract_fields_and_expand(
            schemas[entity_name], schemas
        )

        # Deduplicate preserving order
        seen: set[str] = set()
        clean_fields: list[str] = []
        for f in fields:
            if f not in seen:
                seen.add(f)
                clean_fields.append(f)

        entity_data["fields"] = clean_fields

        # Only set expand if not already explicitly set
        if "expand" not in entity_data and expand_cols:
            seen_e: set[str] = set()
            entity_data["expand"] = [
                e for e in expand_cols if not (seen_e.add(e) or e in seen_e)  # type: ignore[func-returns-value]
            ]
            # Simpler dedup
            entity_data["expand"] = list(dict.fromkeys(expand_cols))

        if entity_name in QUERY_ONLY_ENTITIES:
            entity_data["query_only"] = True
            entity_data.pop("key_fields", None)
            entity_data.pop("key_format", None)
        elif entity_name in KEY_OVERRIDES:
            kf = KEY_OVERRIDES[entity_name]
            entity_data["key_fields"] = kf
            if len(kf) == 1:
                entity_data["key_format"] = f"Single value: <{kf[0]}>"
            else:
                example = "/".join(f"<{k}>" for k in kf)
                entity_data["key_format"] = f"Slash-separated: {example}"
            entity_data.pop("query_only", None)
        else:
            # Heuristic: first field(s) that look like key fields
            candidates = [
                f for f in clean_fields
                if f.endswith(("Nbr", "ID", "CD", "Type"))
                or f in ("DocType", "Module", "BatchNbr")
            ]
            if candidates:
                entity_data.setdefault("key_fields", candidates[:2])
            else:
                entity_data.setdefault("key_fields", [])

        updated += 1

    apply_annotations(catalog)

    # Sort catalog keys alphabetically for readability
    sorted_catalog = dict(sorted(catalog.items()))
    catalog_path.write_text(
        json.dumps(sorted_catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Rebuilt {catalog_path}")
    print(f"  Updated {updated}/{len(catalog)} entities with field data")


if __name__ == "__main__":
    main()
