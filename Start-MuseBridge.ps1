[CmdletBinding()]
param(
    [switch]$Connect,
    [switch]$ActivateExisting,
    [string]$ProjectRoot = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$pythonw = Join-Path $resolvedRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "MuseBridge pythonw.exe was not found at '$pythonw'. Install the project environment first."
}

$arguments = @('-m musebridge.app', '--secondary-screen')
if ($Connect) {
    $arguments += '--connect'
}
if ($ActivateExisting) {
    $arguments += '--activate-existing'
}
$argumentLine = $arguments -join ' '
Start-Process -FilePath $pythonw -ArgumentList $argumentLine -WorkingDirectory $resolvedRoot -WindowStyle Hidden
