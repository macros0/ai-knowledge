<#
.SYNOPSIS
    Start one local process detached, with optional port replacement and logs.

.DESCRIPTION
    Repository fallback for the machine-level opencode helper. The stack
    scripts prefer the global helper when it exists and use this implementation
    otherwise. It is intentionally small: stop the exact PID/port requested by
    the caller, start the requested executable hidden, redirect stdout/stderr,
    and persist the child PID.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$FilePath,
    [object[]]$ArgumentList = @(),
    [Parameter(Mandatory = $true)] [string]$WorkingDirectory,
    [hashtable]$Env = @{},
    [int]$Port = 0,
    [Parameter(Mandatory = $true)] [string]$PidFile,
    [string]$LogDir = (Split-Path -Parent $PidFile)
)

$ErrorActionPreference = 'Stop'

function Stop-Tree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try {
        taskkill /PID $ProcessId /T /F 2>$null | Out-Null
    } catch { }
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$pidPath = [System.IO.Path]::GetFullPath($PidFile)

if (Test-Path -LiteralPath $pidPath) {
    $previous = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($previous -match '^\d+$') {
        $old = Get-Process -Id ([int]$previous) -ErrorAction SilentlyContinue
        if ($old) { Stop-Tree -ProcessId $old.Id }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

if ($Port -gt 0) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        Stop-Tree -ProcessId ([int]$listener.OwningProcess)
    }
}

$safeName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath)
if ([string]::IsNullOrWhiteSpace($safeName)) { $safeName = 'process' }
$stdout = Join-Path $LogDir "$safeName-out.log"
$stderr = Join-Path $LogDir "$safeName-err.log"

# Start-Process inherits the current environment. Apply requested overrides
# only for the synchronous launch call, then restore the parent environment.
$savedEnv = @{}
$createdEnv = @()
foreach ($name in $Env.Keys) {
    $path = "Env:$name"
    if (Test-Path -LiteralPath $path) {
        $savedEnv[$name] = (Get-Item -LiteralPath $path).Value
    } else {
        $createdEnv += $name
    }
    Set-Item -LiteralPath $path -Value ([string]$Env[$name])
}

try {
    $process = Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
} finally {
    foreach ($name in $Env.Keys) {
        $path = "Env:$name"
        if ($savedEnv.ContainsKey($name)) {
            Set-Item -LiteralPath $path -Value $savedEnv[$name]
        } else {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
}

Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
Write-Output "Started $FilePath (PID $($process.Id)); logs: $stdout / $stderr"
