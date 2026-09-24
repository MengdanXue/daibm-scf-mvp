# Create .env with random secrets when it does not exist yet. Idempotent.
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$target = Join-Path $root '.env'
if (Test-Path $target) { exit 0 }
function New-Secret([int]$bytes) {
    $buffer = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ([Convert]::ToBase64String($buffer)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
$demo = 'Demo-' + (New-Secret 9) + '-7a!'
$lines = Get-Content -Encoding utf8 (Join-Path $root '.env.example') | ForEach-Object {
    if ($_ -like 'POSTGRES_PASSWORD=*') { 'POSTGRES_PASSWORD=' + (New-Secret 24) }
    elseif ($_ -like 'DAIBM_DEMO_PASSWORD=*') { 'DAIBM_DEMO_PASSWORD=' + $demo }
    elseif ($_ -like 'DAIBM_METRICS_TOKEN=*') { 'DAIBM_METRICS_TOKEN=' + (New-Secret 24) }
    else { $_ }
}
[System.IO.File]::WriteAllLines($target, $lines)
Write-Host "[DAIBM-SCF] Created .env with random secrets. Demo password: $demo"
