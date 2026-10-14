param(
    [Parameter(Mandatory=$true, Position=0)][ValidateSet('doctor','backup','status','verify','diagnostics')][string]$Command,
    [Parameter(Mandatory=$true)][string]$ArchiveRoot,
    [ValidateSet('all','firefox','brave')][string]$Browser = 'all',
    [string]$FirefoxProfile,
    [string]$BraveProfile,
    [string]$Run,
    [string]$Output,
    [switch]$Latest
)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Run .\setup.ps1 first.' }
$arguments = @((Join-Path $PSScriptRoot 'tools\venice_operator.py'), $Command, '--archive-root', $ArchiveRoot)
if ($Command -in @('doctor','backup')) {
    $arguments += @('--browser', $Browser)
    if ($FirefoxProfile) { $arguments += @('--firefox-profile', $FirefoxProfile) }
    if ($BraveProfile) { $arguments += @('--brave-profile', $BraveProfile) }
}
if ($Run) { $arguments += @('--run', $Run) }
if ($Output) { $arguments += @('--output', $Output) }
if ($Latest) { $arguments += '--latest' }
& $python @arguments
exit $LASTEXITCODE
