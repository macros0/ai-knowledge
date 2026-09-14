<#
.SYNOPSIS
    Verifies that the running backend is the isolated Stage 8 contour.

.DESCRIPTION
    This is a read-only preflight before uploading measurement documents. It
    checks the public health endpoint and compares its runtime profile label
    with the shared Stage 8 profile. It does not modify the database, Qdrant,
    files, or the current shell environment.
#>

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'stage8-test-profile.ps1')
$profile = Get-Stage8TestProfile

Write-Output '=== Stage 8: preflight ==='
Write-Output "Ожидаемый профиль: $($profile.KnowledgeProfile)"
Write-Output "Ожидаемая БД:      $($profile.DatabaseName)"
Write-Output "Ожидаемый Qdrant:  $($profile.CollectionName)"

try {
    # Windows PowerShell 5.1 may decode a JSON response without an explicit
    # charset using the system code page. Force UTF-8 so Cyrillic profile names
    # compare identically when this script is started through .cmd.
    $client = New-Object System.Net.WebClient
    $client.Encoding = [System.Text.Encoding]::UTF8
    $health = $client.DownloadString('http://127.0.0.1:18000/health') | ConvertFrom-Json
} catch {
    throw 'Backend недоступен. Сначала выполните tests\scripts\stage8\start-stage8-test.cmd.'
}

Write-Output "Фактический профиль: $($health.knowledge_profile)"
Write-Output "Health: $($health.status)"
foreach ($name in @('database', 'qdrant', 'ollama', 'llm')) {
    $dependency = $health.dependencies.$name
    Write-Output "  $name`: $($dependency.status)"
}

if ($health.knowledge_profile -ne $profile.KnowledgeProfile) {
    throw "Остановлено: backend работает не в измерительном профиле (получено '$($health.knowledge_profile)')."
}
if ($health.status -ne 'ok') {
    throw "Остановлено: health не готов ($($health.status))."
}
foreach ($name in @('database', 'qdrant', 'ollama', 'llm')) {
    if ($health.dependencies.$name.status -notin @('ok', 'rate_limited')) {
        throw "Остановлено: зависимость $name недоступна."
    }
}

Write-Output 'Готово: можно загружать измерительные документы.'
