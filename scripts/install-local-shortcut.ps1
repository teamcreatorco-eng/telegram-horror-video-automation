$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopPath 'Start Horror Automation.lnk'
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $projectRoot 'start-local.cmd'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Start the local Telegram horror automation. Saved jobs remain resumable.'
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Host "Desktop shortcut created: $shortcutPath"
