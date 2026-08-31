# Start the read-only dashboard, optionally sharing it beyond this machine.
#
#   powershell -ExecutionPolicy Bypass -File serve_web.ps1            just this PC
#   powershell -ExecutionPolicy Bypass -File serve_web.ps1 -Lan       your network
#   powershell -ExecutionPolicy Bypass -File serve_web.ps1 -Tunnel    a private link
#
# Ctrl+C stops the server (and the tunnel with it). -Lan needs a firewall rule
# and an Administrator prompt; -Tunnel needs neither, because the connection is
# made outwards from this machine.

param(
    [int]$Port = 8000,
    [switch]$Lan,
    [switch]$Tunnel,
    [switch]$WithScheduler
)

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# A tunnel reaches the server from this machine, so it only ever needs to
# listen on localhost - nothing is exposed to the network unless -Lan says so.
$bind = '127.0.0.1'
if ($Lan) { $bind = '0.0.0.0' }

if ($Tunnel) {
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Write-Host "cloudflared is not installed. Install it once with:" -ForegroundColor Yellow
        Write-Host "    winget install --id Cloudflare.cloudflared"
        Write-Host "then open a new terminal and run this again."
        exit 1
    }
}

$serverArgs = @("$appDir\web_app.py", '--host', $bind, '--port', "$Port")
if ($WithScheduler) { $serverArgs += '--with-scheduler' }

Write-Host "starting the dashboard on $bind`:$Port ..." -ForegroundColor Cyan
$server = Start-Process -FilePath 'py' -ArgumentList $serverArgs -PassThru -NoNewWindow

try {
    Start-Sleep -Seconds 3
    if ($server.HasExited) {
        Write-Host "the server stopped straight away - run 'py web_app.py' to see why" -ForegroundColor Red
        exit 1
    }

    if ($Lan) {
        $ips = (Get-NetIPAddress -AddressFamily IPv4 |
                Where-Object { $_.IPAddress -notlike '127.*' }).IPAddress
        Write-Host ""
        Write-Host "On your network, share one of these:" -ForegroundColor Green
        foreach ($ip in $ips) { Write-Host "    http://$ip`:$Port" }
        Write-Host "(if nobody can reach it, the firewall rule is missing - see the README)"
    }

    if ($Tunnel) {
        Write-Host ""
        Write-Host "Opening a private link. Share the trycloudflare.com URL below." -ForegroundColor Green
        Write-Host "It is unlisted but not password protected, and it changes every run."
        Write-Host ""
        & cloudflared tunnel --url "http://127.0.0.1:$Port"
    }
    else {
        Write-Host ""
        Write-Host "Dashboard running at http://localhost:$Port  -  Ctrl+C to stop." -ForegroundColor Green
        Wait-Process -Id $server.Id
    }
}
finally {
    if (-not $server.HasExited) {
        Write-Host ""
        Write-Host "stopping the dashboard ..." -ForegroundColor Cyan
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
