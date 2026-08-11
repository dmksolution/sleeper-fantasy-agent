<#
.SYNOPSIS
    Allow other devices on your home network to reach the dashboard.

.DESCRIPTION
    Two things stop another PC or a phone from loading the dashboard:

      1. The server binds to 127.0.0.1 by default, which only accepts
         connections from this machine. Start it with --host 0.0.0.0.
      2. Windows blocks inbound connections to python.exe.

    This script fixes (2) by adding a single inbound rule, scoped as tightly as
    is still useful: only your own subnet, and only on the Private network
    profile, so the port stays shut if you take this machine onto public Wi-Fi.

    It re-launches itself elevated, because firewall rules need admin.

    NOTE: the dashboard has no authentication. Anyone on your home network who
    knows the URL can read your league data and trigger a sync. That is usually
    fine at home and is the reason this rule is not enabled by default.

.PARAMETER Port
    Port the dashboard listens on. Default 8770.

.PARAMETER Remove
    Delete the rule instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\enable_lan_access.ps1 -Port 8781

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\enable_lan_access.ps1 -Remove
#>

[CmdletBinding()]
param(
    [int]$Port = 8770,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$RuleName = "Sleeper Fantasy Dashboard"

# Firewall changes need admin, so relaunch elevated and hand over the arguments.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    # An elevated process runs in a different logon session, and mapped network
    # drives are per-session. If this script lives on S:\ then the elevated
    # window cannot see S:\ at all, fails to find the file, and closes instantly
    # with no visible error. Hand it the UNC path instead.
    $self = $PSCommandPath
    if ($self -match '^([A-Za-z]):') {
        $drive = Get-PSDrive -Name $Matches[1] -ErrorAction SilentlyContinue
        if ($drive -and $drive.DisplayRoot -like '\\*') {
            $self = $drive.DisplayRoot + $self.Substring(2)
        }
    }

    Write-Host "Needs administrator rights. Approve the UAC prompt to continue..."
    $argList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$self`"", '-Port', $Port
    )
    if ($Remove) { $argList += '-Remove' }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList -ErrorAction Stop
    } catch {
        Write-Warning "Elevation was cancelled or failed: $_"
        Write-Host ""
        Write-Host "Run this instead, in a PowerShell started with 'Run as administrator':"
        Write-Host ""
        Write-Host "  New-NetFirewallRule -DisplayName 'Sleeper Fantasy Dashboard' ``"
        Write-Host "    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port ``"
        Write-Host "    -RemoteAddress LocalSubnet -Profile Private"
        Write-Host ""
    }
    return
}

if ($Remove) {
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Write-Host "Removed the firewall rule. The dashboard is local-only again."
    Start-Sleep -Seconds 3
    return
}

# Work out this machine's LAN address and subnet so the rule can be scoped to it
# rather than left open to anything that can route to this host.
$ip = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike '127.*' -and
        $_.IPAddress -notlike '169.254.*' -and
        $_.InterfaceAlias -notlike '*vEthernet*' -and
        $_.InterfaceAlias -notlike '*Loopback*'
    } | Select-Object -First 1

if (-not $ip) { throw "Could not find a LAN IPv4 address on this machine." }

$octets = $ip.IPAddress.Split('.')
$subnet = "$($octets[0]).$($octets[1]).$($octets[2]).0/24"

Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

New-NetFirewallRule -DisplayName $RuleName `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
    -RemoteAddress $subnet -Profile Private `
    -Description "Local fantasy football dashboard, home network only." | Out-Null

# Deliberately not named $profile -- that is a PowerShell automatic variable.
$netCategory = (Get-NetConnectionProfile |
    Where-Object { $_.InterfaceAlias -eq $ip.InterfaceAlias }).NetworkCategory

# Prove the rule actually landed rather than trusting that the call returned.
$check = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if (-not $check) { throw "The rule was not created. Check for Group Policy restrictions." }

Write-Host ""
Write-Host "  Firewall rule added and verified."
Write-Host "  Port      : $Port (TCP inbound)"
Write-Host "  Allowed   : $subnet on the Private profile"
Write-Host "  Interface : $($ip.InterfaceAlias) is currently '$netCategory'"
if ($netCategory -ne 'Private') {
    Write-Warning "  This network is '$netCategory', so the rule will NOT apply until it is set to Private."
}
Write-Host ""
Write-Host "  Now start the dashboard so it listens on the network:"
Write-Host "     python cli.py web --port $Port --host 0.0.0.0"
Write-Host ""
Write-Host "  Then browse from any device on your home network to:"
Write-Host "     http://$($ip.IPAddress):$Port"
Write-Host ""
Write-Host "  To undo:  .\scripts\enable_lan_access.ps1 -Remove"
Write-Host ""
Start-Sleep -Seconds 12
