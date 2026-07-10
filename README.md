# Acumatica MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server for
**Acumatica ERP**. It lets an MCP client — Claude Desktop, or any other MCP-aware
agent — query and act on **any** Acumatica tenant's contract-based REST API.

Acumatica's contract API is beautifully uniform: every entity (SalesOrder, Bill,
Customer, StockItem, …) supports the same `GET / PUT / DELETE` verbs with OData
query parameters, plus `POST /{Entity}/{Action}` for entity-specific actions.
Rather than hand-code hundreds of endpoints as individual tools, this server
exposes **8 generic tools** that cover the entire surface — all ~119 entities in a
standard tenant — and a small catalog that teaches the model each entity's fields,
key format, and actions.

> **Status:** Beta. Battle-tested in day-to-day operations against a live
> production Acumatica ERP before being generalized and open-sourced here.

## The 8 tools

| Tool | HTTP | What it does |
|------|------|--------------|
| `list_entities` | (local) | List the entities available in the tenant, filterable by substring. |
| `describe_entity` | (local) | **Call this first.** Returns an entity's fields, key format, actions, and expandable sub-collections. |
| `list_records` | `GET /{Entity}` | Query records with OData (`$filter`, `$select`, `$top`, `$expand`, …). |
| `get_record` | `GET /{Entity}/{key}` | Fetch a single record by its key. |
| `upsert_record` | `PUT /{Entity}` | Create or update a record. |
| `delete_record` | `DELETE /{Entity}/{key}` | Delete a record. |
| `invoke_action` | `POST /{Entity}/{Action}` | Run an action (Release, Cancel, Confirm, …). |
| `get_schema` | `GET /{Entity}/$adHocSchema` | Discover user-defined (DAC extension) fields and view names. |

Records returned by `list_records` / `get_record` include a `browser_url` field
for common entities, linking straight to the record in the Acumatica web UI.

**Read-only by default.** The three mutating tools (`upsert_record`,
`delete_record`, `invoke_action`) are disabled unless you explicitly opt in — see
[Security notes](#security-notes). Point it at production safely to explore first.

See **[docs/USAGE.md](docs/USAGE.md)** for the field-name, key-format, `$filter`,
and `$custom` rules that make queries reliable.

## Requirements

- An Acumatica instance with the **contract-based REST API** enabled
  (the default `Default` endpoint, e.g. version `24.200.001`).
- A **dedicated service/integration account** with the appropriate role(s).
- Python **3.10+**.

## Install

### Option A — run from a clone (simplest for Claude Desktop)

```bash
git clone https://github.com/yourlastnamesoundslikeatypeofpasta/acumatica-mcp-server.git
cd acumatica-mcp-server
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

### Option B — pip install

```bash
pip install acumatica-mcp-server
```

This installs an `acumatica-mcp-server` console entry point and `python -m
acumatica_mcp.server`.

## Configure

Provide your tenant's connection details **either** via environment variables in
your MCP client config (recommended — no file on disk), **or** via a `.env` file.

Copy [`.env.example`](.env.example) to `.env` and fill it in:

```ini
ACUMATICA_BASE_URL=https://your-instance.acumatica.com
ACUMATICA_ENDPOINT_PATH=/entity/Default/24.200.001
ACUMATICA_USERNAME=service_account
ACUMATICA_PASSWORD=your-password
ACUMATICA_COMPANY=YourCompany
```

## Register with Claude Desktop

Add this to your `claude_desktop_config.json` (full example in
[`docs/claude-desktop-config.example.json`](docs/claude-desktop-config.example.json)):

```json
{
  "mcpServers": {
    "acumatica": {
      "command": "python",
      "args": ["C:/path/to/acumatica-mcp-server/src/acumatica_mcp/server.py"],
      "env": {
        "ACUMATICA_BASE_URL": "https://your-instance.acumatica.com",
        "ACUMATICA_ENDPOINT_PATH": "/entity/Default/24.200.001",
        "ACUMATICA_USERNAME": "service_account",
        "ACUMATICA_PASSWORD": "your-password",
        "ACUMATICA_COMPANY": "YourCompany"
      }
    }
  }
}
```

Restart Claude Desktop, then try: *"List the open sales orders modified in the
last 14 days"* or *"Describe the Bill entity."*

## Regenerating the entity catalog for your tenant

The bundled `entity_catalog.json` covers standard Acumatica entities. If your
tenant has customizations (custom entities, extension fields), regenerate it from
your tenant's OpenAPI spec:

1. In Acumatica, open your endpoint under **Web Service Endpoints** and export /
   download its OpenAPI (Swagger) JSON — or `GET {BASE_URL}{ENDPOINT_PATH}/swagger.json`.
2. Rebuild:
   ```bash
   python src/acumatica_mcp/rebuild_catalog.py path/to/your_openapi_spec.json
   ```
3. Restart the server (the catalog is loaded once at startup).

## How auth works

Cookie-based. The server logs in to `/entity/auth/login` on the first request,
holds the session cookie, transparently re-logs in on a 401, and logs out on exit.

## Security notes

- **Read-only by default.** `upsert_record` and `invoke_action` require
  `ACUMATICA_ALLOW_WRITES=1`; `delete_record` requires `ACUMATICA_ALLOW_DELETES=1`.
  Until you set those, mutating tools return a clear "disabled" message instead of
  touching data. Enable them only when you mean to, ideally on a sandbox tenant.
- **Use a dedicated service account** scoped to only the entities/roles you need,
  never a real person's login. Consider a read-only role in Acumatica as a second
  layer of defense.
- `.env` is git-ignored. Keep credentials out of version control; prefer passing
  them through your MCP client's `env` block.

## Prior art & related projects

This is not the first Acumatica-to-MCP or Acumatica-to-AI project — the Acumatica
community has been busy. If this one doesn't fit your needs, check out:

- [MCP4Acumatica](https://github.com/hallboys/MCP4Acumatica) — a remote MCP server
  (Cloudflare Workers) with per-user OAuth and role-based, read-only access.
- [grp-mcp](https://pypi.org/project/grp-mcp/) — a feature-rich MCP server with
  full CRUD and multiple client planes.
- [easy-acumatica](https://github.com/Nioron07/Easy-Acumatica) — a mature Python
  REST SDK (not MCP) with dynamic model generation.
- [CData Acumatica MCP Server](https://github.com/CDataSoftware/acumatica-mcp-server-by-cdata)
  — a read-only MCP backed by the CData JDBC driver.

This server's angle: a **small, dependency-light, self-hostable** stdio server you
can read top to bottom in one sitting, that works against any tenant with nothing
but a service account.

## License

[MIT](LICENSE) © Christian Zagazeta

## Disclaimer

Not affiliated with or endorsed by Acumatica, Inc. "Acumatica" is a trademark of
its respective owner. Use at your own risk against your own tenants.
