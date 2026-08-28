<#
.SYNOPSIS
    Starts a local Keycloak in Docker (detached) for SSO/OIDC simulation
    and waits until it is ready.

.DESCRIPTION
    Runs `docker run quay.io/keycloak/keycloak:25 start-dev` via the opencode
    start-background.ps1 helper (hidden window, logs to %TEMP%\opencode, kills a
    stale instance by port) and polls the realm discovery endpoint with retries.

    Ports: 8080 is often taken on this machine by 3proxy, so Keycloak listens
    on 8081. Update KEYCLOAK_URL accordingly.

.EXAMPLE
    .\scripts\start-keycloak.ps1
#>

$ErrorActionPreference = 'Stop'

$LogDir = "$env:TEMP\opencode"
$PidFile = Join-Path $LogDir 'keycloak.pid'
$Port = 8081
$Container = 'okf-keycloak'
$Image = 'quay.io/keycloak/keycloak:25.0.0'

# Убить старый контейнер с тем же именем (idempotent).
$ErrorActionPreference = 'SilentlyContinue'
docker rm -f $Container 2>$null | Out-Null
$ErrorActionPreference = 'Stop'

& "$env:USERPROFILE\.config\opencode\scripts\start-background.ps1" `
    -FilePath 'docker' `
    -ArgumentList @(
        'run', '--name', $Container,
        '-p', "8081:8080",
        '-v', 'keycloak-data:/opt/keycloak/data',
        '-e', 'KEYCLOAK_ADMIN=admin',
        '-e', 'KEYCLOAK_ADMIN_PASSWORD=admin',
        $Image,
        'start-dev'
    ) `
    -WorkingDirectory $LogDir `
    -Port $Port `
    -PidFile $PidFile `
    -LogDir $LogDir

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$Port/realms/master/.well-known/openid-configuration" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Output "Keycloak готов на http://localhost:$Port (admin/admin)"
} else {
    Write-Output "WARNING: Keycloak не ответил за 120с. Логи: $LogDir\docker-out.log / docker-err.log"
}