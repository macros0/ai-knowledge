<#
.SYNOPSIS
    Starts local Qdrant (binary, not Docker) as a detached background process
    and waits until it is ready on :6333.

.DESCRIPTION
    Locates the qdrant.exe binary (known location or a fallback search),
    starts it via the opencode start-background.ps1 helper (kills a stale
    instance by PID/port, redirects logs, hidden window) and polls the
    health endpoint until Qdrant responds.

    Storage lives in the directory next to qdrant.exe (./storage), so data
    is preserved between runs.

.EXAMPLE
    .\scripts\start-qdrant.ps1
#>

$ErrorActionPreference = 'Stop'

# Версия бинаря должна быть совместима со storage-форматом данных
# (README: только ±1 минор). Текущий storage написан Qdrant 1.19.0.
$Version = '1.19.0'
$Known = "C:\Users\alexey\AppData\Local\Temp\opencode\qdrant\v$Version\qdrant.exe"
$SearchRoots = @(
    "$env:USERPROFILE\Downloads",
    "$env:USERPROFILE\Desktop",
    "$env:USERPROFILE\Documents",
    "$env:USERPROFILE\scoop\apps",
    "$env:LOCALAPPDATA\Programs"
)

function Find-QdrantExe {
    if (Test-Path -LiteralPath $Known) { return $Known }
    foreach ($root in $SearchRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $hit = Get-ChildItem -LiteralPath $root -Recurse -Depth 4 -Filter 'qdrant.exe' -ErrorAction SilentlyContinue |
            Where-Object { ($_.DirectoryName -match $Version) -or ($_.Length -gt 80000000) } |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    throw "qdrant $Version не найден. Скачайте: https://github.com/qdrant/qdrant/releases (x86_64-pc-windows-msvc) и распакуйте в каталог с v$Version."
}

$Exe = Find-QdrantExe

# Рабочий каталог — корень, где лежит ./storage (данные коллекций).
$StorageRoot = 'C:\Users\alexey\AppData\Local\Temp\opencode\qdrant'
$WorkDir = if (Test-Path -LiteralPath (Join-Path $StorageRoot 'storage')) { $StorageRoot } else { Split-Path -Parent $Exe }
$LogDir = "$env:TEMP\opencode"
$PidFile = Join-Path $LogDir 'qdrant.pid'

Write-Output "Запуск Qdrant: $Exe"

& "$env:USERPROFILE\.config\opencode\scripts\start-background.ps1" `
    -FilePath $Exe `
    -WorkingDirectory $WorkDir `
    -Port 6333 `
    -PidFile $PidFile `
    -LogDir $LogDir

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri 'http://localhost:6333/collections' -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Output "Qdrant готов на http://localhost:6333"
} else {
    Write-Output "WARNING: Qdrant не ответил за 30с. Логи: $LogDir\qdrant-out.log / qdrant-err.log"
}