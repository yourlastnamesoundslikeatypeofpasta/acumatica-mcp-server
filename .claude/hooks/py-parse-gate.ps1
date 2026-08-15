# py-parse-gate.ps1 -- PostToolUse gate for acumatica-mcp-server: byte-compile an edited .py file.
# Catches syntax errors at edit time instead of at server start. Exit 2 feeds the compiler error
# back to the model; anything unreadable exits 0 (fail-open gate, same stance as tool-guard.ps1).
$ErrorActionPreference = 'SilentlyContinue'
try {
    $j = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $f = [string]$j.tool_input.file_path
    if (-not $f -or $f -notmatch '\.py$' -or -not (Test-Path -LiteralPath $f)) { exit 0 }
    $py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'py' }
    $out = & $py -m py_compile $f 2>&1
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("py_compile failed for ${f}:")
        @($out) | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
        exit 2
    }
} catch { }
exit 0
