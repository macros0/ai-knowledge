<#
.SYNOPSIS
    Stop all services of the AI Knowledge stack (Qdrant, Ollama, backend, frontend).

.DESCRIPTION
    Kills processes by PID files in %TEMP%\opencode and by ports 6333/12400/8000/3000.
    PID files are removed afterwards.

.EXAMPLE
    .\scripts\stop-all.ps1
#>

$LogDir = "$env:TEMP\opencode"

Write-Output "=== Остановка по PID-файлам ==="
$PidFiles = @(
    (Join-Path $LogDir 'qdrant.pid'),
    (Join-Path $LogDir 'ollama.pid'),
    (Join-Path $LogDir 'backend.pid'),
    (Join-Path $LogDir 'next.pid')
)
foreach ($f in $PidFiles) {
    if (-not (Test-Path $f)) { continue }
    $pidLine = Get-Content $f -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pidLine -match '^\d+$') {
        try {
            taskkill /PID ([int]$pidLine) /T /F 2>$null | Out-Null
            Write-Output "  stopped PID $pidLine ($([System.IO.Path]::GetFileName($f)))"
        } catch { }
    }
    Remove-Item $f -Force -ErrorAction SilentlyContinue
}

Write-Output "=== Остановка по портам ==="
foreach ($p in 6333, 12400, 8000, 3000) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Output "  stopped port $p (PID $($c.OwningProcess))"
        } catch { }
    }
}

Write-Output "=== Готово ==="
