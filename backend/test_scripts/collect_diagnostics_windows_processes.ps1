param(
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true)][string]$StopPath
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputPath) { throw 'Preserve the previous process metrics' }
$diagCpuWriter = [System.IO.StreamWriter]::new($OutputPath, $false, [System.Text.UTF8Encoding]::new($false))
$diagCpuPrevious = @{}
$diagCpuPreviousTimestamp = $null
try {
    while (-not (Test-Path -LiteralPath $StopPath)) {
        $diagCpuProcesses = @(Get-CimInstance Win32_PerfRawData_PerfProc_Process)
        $diagCpuTimestamp = [uint64]$diagCpuProcesses[0].Timestamp_Sys100NS
        $diagCpuSeconds = if ($null -ne $diagCpuPreviousTimestamp) { ($diagCpuTimestamp - $diagCpuPreviousTimestamp) / 10000000.0 } else { 0 }
        $diagCpuNext = @{}
        $diagCpuRows = @()
        foreach ($diagCpuProcess in $diagCpuProcesses) {
            if ($diagCpuProcess.IDProcess -eq 0) { continue }
            $diagCpuKey = [string]$diagCpuProcess.IDProcess + ':' + $diagCpuProcess.Name
            $diagCpuTime = [uint64]$diagCpuProcess.PercentProcessorTime
            $diagCpuNext[$diagCpuKey] = $diagCpuTime
            if ($diagCpuSeconds -gt 0 -and $diagCpuPrevious.ContainsKey($diagCpuKey) -and $diagCpuTime -gt $diagCpuPrevious[$diagCpuKey]) {
                $diagCpuRows += [PSCustomObject]@{
                    name = $diagCpuProcess.Name
                    pid = [int]$diagCpuProcess.IDProcess
                    cpu_seconds_delta = ($diagCpuTime - $diagCpuPrevious[$diagCpuKey]) / 10000000.0
                    working_set_private_bytes = [uint64]$diagCpuProcess.WorkingSetPrivate
                }
            }
        }
        $diagCpuRecord = [PSCustomObject]@{
            at_unix = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0)
            sample_seconds = $diagCpuSeconds
            logical_processors = [Environment]::ProcessorCount
            scope = 'numeric process CPU only; no command lines or environment'
            processes = @($diagCpuRows | Sort-Object cpu_seconds_delta -Descending | Select-Object -First 16)
        }
        $diagCpuWriter.WriteLine(($diagCpuRecord | ConvertTo-Json -Depth 4 -Compress))
        $diagCpuWriter.Flush()
        $diagCpuPrevious = $diagCpuNext
        $diagCpuPreviousTimestamp = $diagCpuTimestamp
        Start-Sleep -Seconds 2
    }
} finally {
    $diagCpuWriter.Dispose()
}
