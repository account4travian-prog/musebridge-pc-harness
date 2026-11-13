$source = Join-Path $PSScriptRoot 'My Files\input.txt'
$destination = Join-Path $PSScriptRoot output file.txt
Get-Content -Raw $source | Set-Content -NoNewline $destination
