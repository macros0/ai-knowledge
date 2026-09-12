<#
.SYNOPSIS
    Starts local PostgreSQL 17 (portable binaries, not Docker) as a detached
    background process and waits until it is ready on :5432.

.DESCRIPTION
    Locates postgres.exe (known location), starts it via the opencode
    start-background.ps1 helper (kills a stale instance by PID/port, redirects
    logs, hidden window) and polls pg_isready until the server responds. Then
    creates the application database (parsed from DATABASE_URL in .env) if it
    does not exist yet.

    Data directory lives in C:\postgresql17\data (next to the binaries), so
    data is preserved between runs.

    NOTE: postgres.exe refuses to run with Administrator privileges — start
    the stack from a non-elevated shell.

.EXAMPLE
    .\scripts\start-postgres.ps1
#>

$ErrorActionPreference = 'Stop'

$PgRoot = 'C:\postgresql17'
$PgBin = Join-Path $PgRoot 'pgsql\bin'
$PgData = Join-Path $PgRoot 'data'
$PgPort = 5432
$LogDir = "$env:TEMP\opencode"
$PidFile = Join-Path $LogDir 'postgres.pid'

$Exe = Join-Path $PgBin 'postgres.exe'

if (-not (Test-Path -LiteralPath $Exe)) {
    throw "postgres.exe не найден в $Exe. Распакуйте portable-бинарь PostgreSQL в $PgRoot."
}
if (-not (Test-Path -LiteralPath $PgData)) {
    throw "Каталог данных $PgData не найден. Выполните initdb (см. README)."
}

function Get-PgConfigFromEnv {
    $root = Split-Path -Parent $PSScriptRoot
    $envFile = Join-Path $root '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return $null }
    $line = Get-Content -LiteralPath $envFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } |
        Select-Object -First 1
    if (-not $line) { return $null }
    $url = ($line -split '=', 2)[1].Trim()
    if ($url -match '://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?#]+)') {
        return [pscustomobject]@{
            User     = $Matches[1]
            Password = $Matches[2]
            Host     = $Matches[3]
            Port     = $Matches[4]
            DbName   = $Matches[5]
        }
    }
    return $null
}

function Ensure-Database {
    $cfg = Get-PgConfigFromEnv
    if (-not $cfg) {
        Write-Output "  DATABASE_URL не найден в .env — создание БД пропущено."
        return
    }
    $psql = Join-Path $PgBin 'psql.exe'
    $env:PGPASSWORD = $cfg.Password
    try {
        $exists = & $psql -h 127.0.0.1 -p $PgPort -U $cfg.User -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$($cfg.DbName)'" 2>$null
        if (($exists | Out-String).Trim() -eq '1') {
            Write-Output "  БД $($cfg.DbName) уже существует."
            return
        }
        & $psql -h 127.0.0.1 -p $PgPort -U $cfg.User -d postgres -c "CREATE DATABASE `"$($cfg.DbName)`"" 2>&1 | Out-String | Write-Output
        if ($LASTEXITCODE -eq 0) {
            Write-Output "  БД $($cfg.DbName) создана."
        } else {
            Write-Output "  WARNING: не удалось создать БД $($cfg.DbName)."
        }
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

Write-Output "Запуск PostgreSQL: $Exe"

$GlobalHelper = "$env:USERPROFILE\.config\opencode\scripts\start-background.ps1"
$Helper = if (Test-Path -LiteralPath $GlobalHelper -ErrorAction SilentlyContinue) { $GlobalHelper } else { Join-Path $PSScriptRoot 'start-background.ps1' }
& $Helper `
    -FilePath $Exe `
    -ArgumentList @('-D', $PgData, '-p', "$PgPort") `
    -WorkingDirectory $PgRoot `
    -Port $PgPort `
    -PidFile $PidFile `
    -LogDir $LogDir

$pg_isready = Join-Path $PgBin 'pg_isready.exe'
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    & $pg_isready -h 127.0.0.1 -p $PgPort -q 2>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
}

if ($ready) {
    Write-Output "PostgreSQL готов на 127.0.0.1:$PgPort"
    Ensure-Database
} else {
    Write-Output "WARNING: PostgreSQL не ответил за 30с. Логи: $LogDir\postgres-out.log / postgres-err.log"
}
