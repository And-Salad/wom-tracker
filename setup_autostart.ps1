# Adds (or removes) a Startup shortcut so WOM Tracker launches when you log in.
#
#   powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File setup_autostart.ps1 -Remove

param([switch]$Remove)

$appDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup  = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'WOM Tracker.lnk'

if ($Remove) {
    if (Test-Path $linkPath) { Remove-Item $linkPath; "Removed $linkPath" }
    else { "No startup shortcut found." }
    return
}

$pythonw = Join-Path (Split-Path -Parent (Get-Command py).Source) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    # py.exe lives in C:\Windows; find the real interpreter instead.
    $pythonw = (& py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))")
}
if (-not (Test-Path $pythonw)) { throw "Could not locate pythonw.exe" }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath       = $pythonw
$link.Arguments        = '"' + (Join-Path $appDir 'run_wom.pyw') + '"'
$link.WorkingDirectory = $appDir
$link.Description      = 'WOM Tracker - six-hourly Wise Old Man updates'
$link.Save()

"Created $linkPath"
"Target: $pythonw"
