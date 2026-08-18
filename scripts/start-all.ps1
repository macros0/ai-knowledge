<#
.SYNOPSIS
    Start the full AI Knowledge stack: Qdrant, Ollama, backend, frontend.

.DESCRIPTION
    Brings up every dependency with one command:
      1. Qdrant   -> :6333  (reuses scripts/start-qdrant.ps1)
      2. Ollama   -> :12400 (OLLAMA_HOST=127.0.0.1:12400; 11434 is inside the
                             Windows Hyper-V excluded port range on this machine)
      3. Backend  -> :8000  (uvicorn app.main:app from backend/)
      4. Frontend -> :3000  (node node_modules/next/dist/bin/next dev from frontend/)

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

$results['Qdrant'] = Start-Service -Name 'Qdrant' -Url 'http://localhost:6333/collections' -Launch {
    & (Join-Path $PSScriptRoot 'start-qdrant.ps1')
}

$results['Ollama'] = Start-Service -Name 'Ollama' -Url 'http://localhost:12400/api/tags' -Launch {
    & $Helper -FilePath (Get-OllamaExe) -ArgumentList @('serve') `
        -WorkingDirectory $LogDir `
        -Env @{ OLLAMA_HOST = '127.0.0.1:12400' } `
        -Port 12400 `
        -PidFile (Join-Path $LogDir 'ollama.pid')
}

$results['Backend'] = Start-Service -Name 'Backend' -Url 'http://localhost:8000/health' -Launch {
    & $Helper -FilePath 'python' -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000') `
        -WorkingDirectory (Join-Path $Root 'backend') `
        -Port 8000 `
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
