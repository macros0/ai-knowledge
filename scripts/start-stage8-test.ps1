<#
.SYNOPSIS
    Starts the isolated Stage 8 measurement contour.

.DESCRIPTION
    Starts the common local dependencies, ensures the dedicated PostgreSQL
    database exists, and replaces only the backend process with one configured
    for the Stage 8 test database, Qdrant collection, and data directory.

    The regular contour remains recoverable with scripts\start-all.ps1.
#>

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $env:TEMP 'opencode'
. (Join-Path $PSScriptRoot 'stage8-test-profile.ps1')
$profile = Get-Stage8TestProfile
$TestDatabase = $profile.DatabaseName
$TestCollection = $profile.CollectionName
$TestDataDir = $profile.DataDir

Write-Output '=== Stage 8: изолированный измерительный контур ==='
Write-Output 'Поднимаю общие зависимости и frontend...'
& (Join-Path $PSScriptRoot 'start-all.ps1')
if (-not $?) {
    throw 'Общий стек не запустился.'
}

function Ensure-TestDatabase {
    $psql = 'C:\postgresql17\pgsql\bin\psql.exe'
    if (-not (Test-Path -LiteralPath $psql)) { throw "psql не найден: $psql" }

    $env:PGPASSWORD = $profile.DatabasePassword
    try {
        $exists = & $psql -h $profile.DatabaseHost -p $profile.DatabasePort -U $profile.DatabaseUser -d postgres `
            -tAc "SELECT 1 FROM pg_database WHERE datname = '$TestDatabase'" 2>$null
        if (($exists | Out-String).Trim() -eq '1') {
            Write-Host "Тестовая БД $TestDatabase уже существует."
        } else {
            & $psql -h $profile.DatabaseHost -p $profile.DatabasePort -U $profile.DatabaseUser -d postgres `
                -c "CREATE DATABASE `"$TestDatabase`"" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Не удалось создать БД $TestDatabase." }
            Write-Host "Тестовая БД $TestDatabase создана."
        }
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

    New-Item -ItemType Directory -Path (Join-Path $Root $TestDataDir) -Force | Out-Null
    return $profile.DatabaseUrl
}

$null = Ensure-TestDatabase
$helper = Join-Path $env:USERPROFILE '.config\opencode\scripts\start-background.ps1'
if (-not (Test-Path -LiteralPath $helper -ErrorAction SilentlyContinue)) {
    $helper = Join-Path $PSScriptRoot 'start-background.ps1'
}

$backend = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $backend)) { throw "Python backend не найден: $backend" }

$overrides = $profile.Environment

Write-Output 'Переключаю backend на измерительный контур...'
& $helper -FilePath $backend `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '18000') `
    -WorkingDirectory (Join-Path $Root 'backend') `
    -Env $overrides `
    -Port 18000 `
    -PidFile (Join-Path $LogDir 'backend.pid')

$healthy = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18000/health' -TimeoutSec 4
        if ($health.status -eq 'ok' -and $health.knowledge_profile -eq $profile.KnowledgeProfile) {
            $healthy = $true
            break
        }
    } catch { }
}
if (-not $healthy) { throw 'Backend измерительного контура не стал healthy за 40 секунд.' }

Write-Output ''
Write-Output '=== Измерительный контур готов ==='
Write-Output "  PostgreSQL: $TestDatabase"
Write-Output "  Qdrant:     $TestCollection"
Write-Output "  Файлы:      $TestDataDir"
Write-Output '  Глоссарий:  включён'
Write-Output '  UI:         http://localhost:16300'
Write-Output '  Возврат:    scripts\start-all.ps1'
