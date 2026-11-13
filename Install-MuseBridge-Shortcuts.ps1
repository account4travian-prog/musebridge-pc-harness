[CmdletBinding()]
param(
    [string]$ProjectRoot = $PSScriptRoot,
    [string]$DesktopDirectory,
    [string]$ProgramsDirectory
)

$ErrorActionPreference = 'Stop'

function Get-AbsoluteDirectory([string]$Path, [string]$Label) {
    if (-not $Path -or -not [System.IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an absolute directory path."
    }
    return [System.IO.Path]::GetFullPath($Path)
}

$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$pythonw = Join-Path $resolvedRoot '.venv\Scripts\pythonw.exe'
$icon = Join-Path $resolvedRoot 'musebridge\assets\musebridge.ico'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "MuseBridge pythonw.exe was not found at '$pythonw'. Install the project environment first."
}

if (-not $DesktopDirectory) {
    $DesktopDirectory = [Environment]::GetFolderPath('Desktop')
}
if (-not $ProgramsDirectory) {
    $ProgramsDirectory = [Environment]::GetFolderPath('Programs')
}
$desktop = Get-AbsoluteDirectory $DesktopDirectory 'DesktopDirectory'
$programs = Get-AbsoluteDirectory $ProgramsDirectory 'ProgramsDirectory'
$menu = Join-Path $programs 'MuseBridge'
$links = @(
    @{ Path = Join-Path $desktop 'MuseBridge Harness.lnk'; Directory = $desktop },
    @{ Path = Join-Path $menu 'MuseBridge Harness.lnk'; Directory = $menu }
)

$shell = New-Object -ComObject WScript.Shell
$expectedTarget = [System.IO.Path]::GetFullPath($pythonw)
$ownedDescription = 'Open MuseBridge Harness on the secondary display and connect.'
foreach ($link in $links) {
    if (Test-Path -LiteralPath $link.Path) {
        $existing = $shell.CreateShortcut($link.Path)
        $existingTarget = $existing.TargetPath
        $sameTarget = $existingTarget -and [System.IO.Path]::GetFullPath($existingTarget) -ieq $expectedTarget
        $sameWorkingDirectory = $existing.WorkingDirectory -and
            [System.IO.Path]::GetFullPath($existing.WorkingDirectory) -ieq $resolvedRoot
        $sameApplication = $existing.Arguments -like '-m musebridge.app*'
        if (-not ($sameTarget -and $sameWorkingDirectory -and $sameApplication -and
                $existing.Description -eq $ownedDescription)) {
            throw "Refusing to overwrite a shortcut not owned by this MuseBridge project: '$($link.Path)'"
        }
    }
}

$argumentLine = '-m musebridge.app --secondary-screen --connect --activate-existing'
foreach ($link in $links) {
    [System.IO.Directory]::CreateDirectory($link.Directory) | Out-Null
    $shortcut = $shell.CreateShortcut($link.Path)
    $shortcut.TargetPath = $expectedTarget
    $shortcut.Arguments = $argumentLine
    $shortcut.WorkingDirectory = $resolvedRoot
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = $ownedDescription
    $shortcut.Save()
}

[pscustomobject]@{
    Installed = $links.Path
    Target = $expectedTarget
    WorkingDirectory = $resolvedRoot
    Arguments = $argumentLine
    Icon = "$icon,0"
} | ConvertTo-Json -Compress
