<#
.SYNOPSIS
    Start the full AI Knowledge stack: Qdrant, Ollama, backend, frontend.

.DESCRIPTION
    Brings up every dependency with one command:
      1. Qdrant    -> :16333 (REST; gRPC :16334). Local binary listens on
                              16333/16334, NOT the Qdrant default 6333/6334 —
                              those fall into the Windows Hyper-V/WSL excluded
                              port range on this machine. Reuses scripts/start-qdrant.ps1.
      2. Ollama    -> :12400 (OLLAMA_HOST=127.0.0.1:12400; 11434 is inside the
                              Windows Hyper-V excluded port range on this machine)
      3. PostgreSQL -> :5432 (reuses scripts/start-postgres.ps1, portable binary)
      4. Backend   -> :18000 (uvicorn app.main:app from backend/; 8000 is inside the
                              Windows Hyper-V/WSL excluded port range on this machine)
      5. Frontend  -> :3000  (node node_modules/next/dist/bin/next dev from frontend/)

    Each step polls its health endpoint with retries instead of sleeping blind.
    Idempotent: a previous instance is killed by PID file and/or port first.

.EXAMPLE
    .\scripts\start-all.ps1
#>

$ErrorActionPreference = 'Stop'

$Helper = "$env:USERPROFILE\.config\opencode\scripts\start-background.ps1"
$LogDir = "$env:TEMP\opencode"
$Root = Split-Path -Parent $PSScriptRoot

function Get-OllamaExe {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $known = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path $known) { return $known }
    throw 'Ollama не найден. Установите https://ollama.com/download'
}

# Host-порты фиксированных сервисов стека. Держим выше динамического диапазона
# Windows (1024-15000): HNS/WSL резервирует диапазоны из него от загрузки к загрузке,
# и порт внутри исключённого блока не может быть забинден (winerror 10013).
# Проверка: netsh interface ipv4 show excludedportrange protocol=tcp
function Assert-PortsAvailable {
    $ports = 16333, 12400, 5432, 18000, 3000
    $excluded = @{}
    try {
        $out = & netsh interface ipv4 show excludedportrange protocol=tcp 2>$null
        foreach ($line in $out) {
            if ($line -match '^\s*(\d{2,5})\s+(\d{2,5})\s*$') {
                $s = [int]$Matches[1]; $e = [int]$Matches[2]
                foreach ($p in $ports) {
                    if ($p -ge $s -and $p -le $e) { $excluded[$p] = "$s-$e" }
                }
            }
        }
    } catch { }
    foreach ($p in $ports) {
        if ($excluded.ContainsKey($p)) {
            Write-Output "[FAIL] Порт $p зарезервирован Windows/HNS (диапазон $($excluded[$p]))."
            Write-Output "       Резервации меняются от загрузки к загрузке (WSL/Hyper-V). См. AGENTS.md — квирк про порты."
            Write-Output "       Проверить: netsh interface ipv4 show excludedportrange protocol=tcp"
            return $false
        }
    }
    return $true
}

function Wait-Health {
    param(
        [string]$Name,
        [string]$Url,
        [int]$Retries = 40,
        [int]$DelaySec = 1
    )
    for ($i = 0; $i -lt $Retries; $i++) {
        Start-Sleep -Seconds $DelaySec
        try {
            $r = Invoke-WebRequest -Uri $Url -TimeoutSec 4 -UseBasicParsing
            if ($r.StatusCode -eq 200) {
                Write-Output "[OK] $Name  ->  $Url"
                return $true
            }
        } catch { }
    }
    Write-Output "[FAIL] $Name  не ответил за $Retries c: $Url"
    return $false
}

function Wait-PgReady {
    param([int]$Retries = 10, [int]$DelaySec = 1)
    $pg_isready = 'C:\postgresql17\pgsql\bin\pg_isready.exe'
    if (-not (Test-Path -LiteralPath $pg_isready)) {
        Write-Output "[FAIL] PostgreSQL  не найден pg_isready.exe (установите portable-бинарь)"
        return $false
    }
    for ($i = 0; $i -lt $Retries; $i++) {
        Start-Sleep -Seconds $DelaySec
        & $pg_isready -h 127.0.0.1 -p 5432 -q 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Output "[OK] PostgreSQL  ->  pg_isready 127.0.0.1:5432"
            return $true
        }
    }
    Write-Output "[FAIL] PostgreSQL  не ответил за $Retries c (pg_isready 127.0.0.1:5432)"
    return $false
}

function Start-Service {
    param([string]$Name, [scriptblock]$Launch, [string]$Url)
    Write-Output ""
    Write-Output "=== $Name ==="
    try {
        & $Launch | ForEach-Object { Write-Output "  $_" }
    } catch {
        Write-Output "  [ERROR] $($_.Exception.Message)"
        return $false
    }
    return Wait-Health -Name $Name -Url $Url
}

$results = [ordered]@{}

if (-not (Assert-PortsAvailable)) {
    Write-Output ""
    Write-Output "WARNING: часть фиксированных портов зарезервирована Windows/HNS — стек не поднят."
    Write-Output "         См. AGENTS.md (квирк про порты) и netsh interface ipv4 show excludedportrange."
    exit 1
}

$results['Qdrant'] = Start-Service -Name 'Qdrant' -Url 'http://localhost:16333/collections' -Launch {
    & (Join-Path $PSScriptRoot 'start-qdrant.ps1')
}

$results['Ollama'] = Start-Service -Name 'Ollama' -Url 'http://localhost:12400/api/tags' -Launch {
    & $Helper -FilePath (Get-OllamaExe) -ArgumentList @('serve') `
        -WorkingDirectory $LogDir `
        -Env @{ OLLAMA_HOST = '127.0.0.1:12400' } `
        -Port 12400 `
        -PidFile (Join-Path $LogDir 'ollama.pid')
}

Write-Output ""
Write-Output "=== PostgreSQL ==="
try {
    & (Join-Path $PSScriptRoot 'start-postgres.ps1') | ForEach-Object { Write-Output "  $_" }
    $results['Postgres'] = Wait-PgReady
} catch {
    Write-Output "  [ERROR] $($_.Exception.Message)"
    $results['Postgres'] = $false
}

$results['Backend'] = Start-Service -Name 'Backend' -Url 'http://localhost:18000/health' -Launch {
    & $Helper -FilePath (Join-Path $Root 'backend\.venv\Scripts\python.exe') -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '18000') `
        -WorkingDirectory (Join-Path $Root 'backend') `
        -Port 18000 `
        -PidFile (Join-Path $LogDir 'backend.pid')
}

$results['Frontend'] = Start-Service -Name 'Frontend' -Url 'http://localhost:3000' -Launch {
    & $Helper -FilePath 'node' -ArgumentList @('node_modules/next/dist/bin/next', 'dev') `
        -WorkingDirectory (Join-Path $Root 'frontend') `
        -Port 3000 `
        -PidFile (Join-Path $LogDir 'next.pid')
}

Write-Output ""
Write-Output "=== Итог ==="
$allOk = $true
foreach ($k in $results.Keys) {
    $state = if ($results[$k]) { 'ready' } else { 'FAIL' }
    if (-not $results[$k]) { $allOk = $false }
    Write-Output ("  {0,-10} {1}" -f $k, $state)
}

if ($allOk) {
    Write-Output ""
    Write-Output "Стек готов. UI: http://localhost:3000"
} else {
    Write-Output ""
    Write-Output "WARNING: часть сервисов не ответила. Логи:"
    Get-ChildItem (Join-Path $LogDir '*.log') -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Output "  $($_.FullName)"
    }
}
