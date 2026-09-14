<#
.SYNOPSIS
    DNS Relay 的 Wireshark 抓包演示脚本。

.DESCRIPTION
    本脚本只负责启动 DNS Relay 并依次执行三组 nslookup。
    抓包仍由 Wireshark 完成，不会把 Wireshark 功能写进中继器。

    使用前：
    1. 以管理员身份打开 PowerShell。
    2. 在 Wireshark 中同时勾选 Npcap Loopback Adapter 和当前联网网卡。
    3. 使用捕获过滤器 udp port 53 后开始抓包。
#>

[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Upstream = "114.114.114.114",
    [int]$PauseSeconds = 2
)

$ErrorActionPreference = "Stop"
$projectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$relayScript = Join-Path $projectDirectory "dns_relay.py"
$configFile = Join-Path $projectDirectory "dnsrelay.txt"
$stdoutLog = Join-Path $projectDirectory "wireshark-relay-output.log"
$stderrLog = Join-Path $projectDirectory "wireshark-relay-error.log"

if (-not (Test-Path $relayScript)) {
    throw "找不到 DNS Relay：$relayScript"
}

if (-not (Test-Path $configFile)) {
    throw "找不到配置文件：$configFile"
}

$administrator = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )

if (-not $administrator) {
    Write-Warning "当前 PowerShell 不是管理员会话。若 Wireshark/Npcap 无法抓包，请改用管理员身份运行。"
}

$occupied = Get-NetUDPEndpoint -LocalPort 53 -ErrorAction SilentlyContinue
if ($occupied) {
    Write-Warning "UDP 53 当前已被占用。请先停止占用端口的 DNS 服务，再重新运行。"
    $occupied | Format-Table LocalAddress, LocalPort, OwningProcess
    exit 1
}

$arguments = @(
    ('"' + $relayScript + '"'),
    "-dd",
    "--host", "127.0.0.1",
    "--port", "53",
    $Upstream,
    ('"' + $configFile + '"')
)

Write-Host "启动 DNS Relay：127.0.0.1:53 -> $Upstream:53"
$relay = Start-Process `
    -FilePath $Python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectDirectory `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -NoNewWindow `
    -PassThru

try {
    Start-Sleep -Seconds 2

    if ($relay.HasExited) {
        throw "DNS Relay 启动失败。请检查 $stderrLog"
    }

    $tests = @(
        @{ Name = "本地解析"; Domain = "www.bupt.com.cn" },
        @{ Name = "黑名单"; Domain = "www.666.com" },
        @{ Name = "上游转发"; Domain = "www.baidu.com" }
    )

    foreach ($test in $tests) {
        Write-Host ""
        Write-Host "[$($test.Name)] nslookup $($test.Domain) 127.0.0.1"
        nslookup $test.Domain 127.0.0.1
        Start-Sleep -Seconds $PauseSeconds
    }

    Write-Host ""
    Write-Host "三组查询已发送。现在停止 Wireshark，保存为 dns-relay-demo.pcapng。"
    Write-Host "Relay 日志：$stdoutLog"
}
finally {
    if ($relay -and -not $relay.HasExited) {
        Stop-Process -Id $relay.Id
    }
}
