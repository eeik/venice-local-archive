param([string]$Wheelhouse)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$setupTemp = Join-Path $PSScriptRoot '.setup-tmp'
New-Item -ItemType Directory -Force -Path $setupTemp | Out-Null
$env:TEMP = $setupTemp
$env:TMP = $setupTemp
$basePython = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { throw 'Install 64-bit Python 3.12.' }
$baseArgs = if ($basePython -eq 'py') { @('-3.12') } else { @() }
$version = & $basePython @baseArgs --version 2>&1
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^Python 3\.12\.') { throw 'Install 64-bit Python 3.12.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { & $basePython @baseArgs -m venv .venv }
$pip = '.venv\Scripts\python.exe'
& $pip -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip in .venv.' }
if ($Wheelhouse) {
    & $pip -m pip install --no-deps --no-index --find-links (Resolve-Path -LiteralPath $Wheelhouse) -r requirements-lock.txt
} else {
    & $pip -m pip install --no-deps -r requirements-lock.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $pip -c 'import cffi, cramjam, dfindexeddb, nacl, snappy, zstd'
if ($LASTEXITCODE -ne 0) { throw 'Dependency import check failed.' }
Write-Host 'Setup complete.'
