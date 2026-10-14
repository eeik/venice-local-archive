param([string]$Output = (Join-Path $PSScriptRoot '..\wheelhouse'))
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputPath = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
python -m pip download --no-deps --only-binary=:all: --dest $outputPath -r (Join-Path $root 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Wheel download failed.' }
Copy-Item -LiteralPath (Join-Path $root 'requirements-lock.txt') -Destination $outputPath
Copy-Item -LiteralPath (Join-Path $root 'THIRD_PARTY_NOTICES.md') -Destination $outputPath
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $outputPath
