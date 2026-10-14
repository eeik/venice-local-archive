<#
.SYNOPSIS
Copy only the Venice browser storage needed for an offline recovery.
.EXAMPLE
.\venice_snapshot.ps1 -Browser firefox -Profile 'C:\Users\me\AppData\Roaming\Mozilla\Firefox\Profiles\abc.default-release' -Output 'D:\Backups\Firefox-Venice-2026-09-24'
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('firefox','brave')][string]$Browser,
    [Parameter(Mandatory=$true)][string]$Profile,
    [Parameter(Mandatory=$true)][string]$Output,
    [switch]$AllowEmpty
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $Profile).Path
$target = [System.IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $target) { throw "Refusing to overwrite $target" }
if ($target.StartsWith($source + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output cannot be inside the source profile'
}
if (Get-Process -Name $Browser -ErrorAction SilentlyContinue) {
    throw "Close $Browser completely before making a consistent copy"
}
if ($Browser -eq 'firefox') {
    $origin = 'storage\default\https+++venice.ai'
    if (Test-Path -LiteralPath (Join-Path $source $origin) -PathType Container) {
        $parts = @($origin)
    } elseif ($AllowEmpty) {
        $parts = @()
    } else {
        throw 'Firefox Venice origin is absent; pass -AllowEmpty only when recording a post-reset empty baseline'
    }
    $looseFiles = @()
} else {
    # Chromium's LevelDB files mix origins. The extractor selects Venice keys,
    # but the raw snapshot intentionally retains other origins' localStorage.
    $idbRoot = Join-Path $source 'IndexedDB'
    $veniceEntries = if (Test-Path -LiteralPath $idbRoot -PathType Container) {
        @(Get-ChildItem -LiteralPath $idbRoot -Force | Where-Object { $_.Name -like 'https_venice.ai_0.indexeddb*' })
    } else { @() }
    if ($veniceEntries.Count -eq 0 -and -not $AllowEmpty) { throw 'No Venice IndexedDB files or directories found; pass -AllowEmpty only for a post-reset empty baseline' }
    $parts = @($veniceEntries | Where-Object PSIsContainer | ForEach-Object { Join-Path 'IndexedDB' $_.Name })
    if ($veniceEntries.Count -gt 0) { $parts += 'Local Storage\leveldb' }
    $looseFiles = @($veniceEntries | Where-Object { -not $_.PSIsContainer } | ForEach-Object { Join-Path 'IndexedDB' $_.Name })
    if ($veniceEntries.Count -gt 0 -and (Test-Path -LiteralPath (Join-Path $source 'File System') -PathType Container)) {
        $parts += 'File System'
    }
}
foreach ($part in $parts) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $part) -PathType Container)) {
        throw "Required Venice directory missing: $part"
    }
}
New-Item -ItemType Directory -Path $target | Out-Null
$statusFile = Join-Path $target 'snapshot-status.json'
@{ status='incomplete'; browser=$Browser; startedAtUtc=[DateTime]::UtcNow.ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $statusFile -Encoding utf8
$hashLines = [System.Collections.Generic.List[string]]::new()
$partSummaries = [System.Collections.Generic.List[object]]::new()
foreach ($part in $parts) {
    $from = Join-Path $source $part
    $to = Join-Path $target $part
    New-Item -ItemType Directory -Path $to -Force | Out-Null
    & robocopy $from $to /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "robocopy failed for $part with exit code $LASTEXITCODE" }
    if (Get-Process -Name $Browser -ErrorAction SilentlyContinue) {
        throw "$Browser started during the copy; this snapshot is incomplete"
    }
    $sourceFiles = @(Get-ChildItem -LiteralPath $from -File -Recurse -Force | Sort-Object FullName)
    $targetFiles = @(Get-ChildItem -LiteralPath $to -File -Recurse -Force)
    $sourceBytes = ($sourceFiles | Measure-Object -Property Length -Sum).Sum
    $targetBytes = ($targetFiles | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sourceBytes) { $sourceBytes = 0 }
    if ($null -eq $targetBytes) { $targetBytes = 0 }
    if ($sourceFiles.Count -ne $targetFiles.Count -or $sourceBytes -ne $targetBytes) {
        throw "Copy verification failed for $part"
    }
    foreach ($file in $sourceFiles) {
        $relative = $file.FullName.Substring($source.Length).TrimStart('\')
        $copied = Join-Path $target $relative
        if (-not (Test-Path -LiteralPath $copied -PathType Leaf)) {
            throw "Copied file missing: $relative"
        }
        if ($file.Length -ne (Get-Item -LiteralPath $copied -Force).Length) {
            throw "Copied file length mismatch: $relative"
        }
        $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        $copiedHash = (Get-FileHash -LiteralPath $copied -Algorithm SHA256).Hash
        if ($sourceHash -ne $copiedHash) {
            throw "Copied file SHA-256 mismatch: $relative"
        }
        $hashLines.Add("$($copiedHash.ToLowerInvariant())  $($relative.Replace('\','/'))")
    }
    $partSummaries.Add(@{ part=$part; files=$sourceFiles.Count; bytes=$sourceBytes })
    Write-Output "SHA-256 verified ${part}: $($sourceFiles.Count) files, $sourceBytes bytes"
}
foreach ($relative in $looseFiles) {
    $from = Join-Path $source $relative
    $to = Join-Path $target $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force | Out-Null
    Copy-Item -LiteralPath $from -Destination $to -ErrorAction Stop
    if (Get-Process -Name $Browser -ErrorAction SilentlyContinue) {
        throw "$Browser started during the copy; this snapshot is incomplete"
    }
    $sourceHash = (Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash
    $copiedHash = (Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash
    if ($sourceHash -ne $copiedHash) { throw "Copied file SHA-256 mismatch: $relative" }
    $hashLines.Add("$($copiedHash.ToLowerInvariant())  $($relative.Replace('\','/'))")
    $partSummaries.Add(@{ part=$relative; files=1; bytes=(Get-Item -LiteralPath $from -Force).Length })
    Write-Output "SHA-256 verified file: $relative"
}
if (Get-Process -Name $Browser -ErrorAction SilentlyContinue) {
    throw "$Browser started before snapshot completion; this snapshot is incomplete"
}
$hashLines.Sort([StringComparer]::Ordinal)
$hashManifest = Join-Path $target 'snapshot-manifest.sha256'
if ($hashLines.Count) {
    $hashLines | Set-Content -LiteralPath $hashManifest -Encoding utf8
} else {
    [System.IO.File]::WriteAllText($hashManifest, '')
}
@{
    status='complete'
    browser=$Browser
    sourceProfile=$source
    completedAtUtc=[DateTime]::UtcNow.ToString('o')
    parts=@($partSummaries)
    files=$hashLines.Count
    sha256Manifest='snapshot-manifest.sha256'
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusFile -Encoding utf8
Write-Output "Closed-browser Venice snapshot ready: $target"
