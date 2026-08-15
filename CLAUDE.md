# acumatica-mcp-server — Claude Code notes

Python MCP server for Acumatica ERP: 8 generic tools over any tenant's contract-based REST API.
Source in `src/acumatica_mcp/`, packaged with setuptools (`pyproject.toml`), published as the
`acumatica` plugin's remote backend.

## Commands (verified 2026-08-15)
- Syntax gate: every `.py` edit is byte-compiled automatically (PostToolUse hook).
- Install dev: `pip install -e .`
- Run local: `python -m acumatica_mcp` (reads `.env` — see `.env.example`)

## Rules this repo earned
- **Remote vs local:** the PRODUCTION server runs remotely (AcumaticaRuviraRemoteMCP scheduled
  task). Analyzing or editing the local checkout says nothing about what is live — after any
  deploy, re-list tools from a client and verify the new behavior (a session once analyzed the
  local server while the remote was the one misbehaving).
- **Auth:** tenant credentials live in `.env` / the remote host's environment. Never print them;
  never commit `.env`. Pause and ask for manual auth rather than entering credentials anywhere.
- **Write tools are gated** — keep it that way. Any new tool that mutates ERP state needs the
  same gating/audit treatment as the existing write tools.
- Version bumps: `pyproject.toml` version + `CHANGELOG.md` entry together.
