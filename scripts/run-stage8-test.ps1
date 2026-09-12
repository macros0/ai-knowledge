<#
.SYNOPSIS
    Runs one Stage 8 Python measurement script against the isolated contour.

.DESCRIPTION
    Applies the same database, Qdrant collection, data directory, profile label,
    and glossary flag as start-stage8-test.ps1 for the lifetime of the child
    Python process. It does not change the current shell environment.
#>

param(
    [Parameter(Mandatory = $true)] [string]$PythonScript,
    [string]$PythonArgumentLine = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'stage8-test-profile.ps1')
$profile = Get-Stage8TestProfile

$scriptPath = if ([IO.Path]::IsPathRooted($PythonScript)) {
    $PythonScript
} else {
    Join-Path $Root $PythonScript
}
$scriptPath = [IO.Path]::GetFullPath($scriptPath)
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Python script not found: $scriptPath"
}

$python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python backend not found: $python"
}

$PythonArgs = if ([string]::IsNullOrWhiteSpace($PythonArgumentLine)) {
    @()
} else {
    [regex]::Matches($PythonArgumentLine, '"(?:[^"]|"")*"|\S+') |
        ForEach-Object { $_.Value.Trim('"') }
}

$saved = @{}
$created = @()
try {
    foreach ($name in $profile.Environment.Keys) {
        $envPath = "Env:$name"
        if (Test-Path -LiteralPath $envPath) {
            $saved[$name] = (Get-Item -LiteralPath $envPath).Value
        } else {
            $created += $name
        }
        Set-Item -LiteralPath $envPath -Value ([string]$profile.Environment[$name])
    }

    Push-Location (Join-Path $Root 'backend')
    try {
        Write-Output "Профиль: $($profile.KnowledgeProfile)"
        Write-Output "БД: $($profile.DatabaseName); Qdrant: $($profile.CollectionName)"
        & $python $scriptPath @PythonArgs
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in $profile.Environment.Keys) {
        $envPath = "Env:$name"
        if ($saved.ContainsKey($name)) {
            Set-Item -LiteralPath $envPath -Value $saved[$name]
        } else {
            Remove-Item -LiteralPath $envPath -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($null -eq $exitCode) { $exitCode = 0 }
exit $exitCode
