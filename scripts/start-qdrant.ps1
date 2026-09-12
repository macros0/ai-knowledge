<#
.SYNOPSIS
    Starts local Qdrant (binary, not Docker) as a detached background process
    and waits until it is ready on :16333.

.DESCRIPTION
    Locates the qdrant.exe binary (known location or a fallback search),
    starts it via the opencode start-background.ps1 helper (kills a stale
    instance by PID/port, redirects logs, hidden window) and polls the
    health endpoint until Qdrant responds.

    Listens on 16333 (REST) / 16334 (gRPC), NOT the default 6333/6334:
    on this machine those fall into the Windows Hyper-V/WSL excluded port
    range (netsh interface ipv4 show excludedportrange) and cannot be bound
    (os error 10013). Ports above the dynamic TCP range (1024-15000) are not
    reserved by HNS, so 16333/16334 are stable. Storage lives in the
    directory next to qdrant.exe (./storage), so data is preserved between
    runs.

.EXAMPLE
    .\scripts\start-qdrant.ps1
#>

$ErrorActionPreference = 'Stop'

# Порты REST/gRPC. НЕ 6333/6334 (дефолт Qdrant): эти порты попадают в исключённый
# диапазон Windows Hyper-V/WSL (netsh interface ipv4 show excludedportrange) →
# bind падает с os error 10013. 16333/16334 выше динамического диапазона TCP.
$HttpPort = '16333'
$GrpcPort = '16334'
$HealthUrl = "http://localhost:$HttpPort/collections"

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

# QDRANT__SERVICE__HOST: биндим только loopback (SECURITY.md §1/§3 — база знаний
# не должна быть достижима с других машин; без этого Qdrant слушает 0.0.0.0).
$GlobalHelper = "$env:USERPROFILE\.config\opencode\scripts\start-background.ps1"
$Helper = if (Test-Path -LiteralPath $GlobalHelper -ErrorAction SilentlyContinue) { $GlobalHelper } else { Join-Path $PSScriptRoot 'start-background.ps1' }
& $Helper `
    -FilePath $Exe `
    -WorkingDirectory $WorkDir `
    -Env @{
        'QDRANT__SERVICE__HOST'       = '127.0.0.1'
        'QDRANT__SERVICE__HTTP_PORT'  = $HttpPort
        'QDRANT__SERVICE__GRPC_PORT'  = $GrpcPort
    } `
    -Port ([int]$HttpPort) `
    -PidFile $PidFile `
    -LogDir $LogDir

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Output "Qdrant готов на $HealthUrl (gRPC :$GrpcPort)"
} else {
    Write-Output "WARNING: Qdrant не ответил за 30с. Логи: $LogDir\qdrant-out.log / qdrant-err.log"
}
