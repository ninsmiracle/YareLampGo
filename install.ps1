$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = if ($env:LAMPGO_INSTALL_LOG_DIR) {
    $env:LAMPGO_INSTALL_LOG_DIR
} else {
    Join-Path $env:USERPROFILE ".lampgo\logs"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$BootstrapLog = Join-Path $LogDir ("bootstrap-{0}-{1}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $PID)
Start-Transcript -Path $BootstrapLog -Force | Out-Null
Write-Host "[LampGo] 启动日志：$BootstrapLog"

try {
    $UvVersion = if ($env:LAMPGO_UV_VERSION) { $env:LAMPGO_UV_VERSION } else { "0.11.29" }
    $ForceUvBootstrap = $env:LAMPGO_FORCE_UV_BOOTSTRAP -eq "1"
    $UvInstallDir = if ($env:LAMPGO_UV_INSTALL_DIR) {
        $env:LAMPGO_UV_INSTALL_DIR
    } else {
        Join-Path $env:USERPROFILE ".lampgo\tools\uv-$UvVersion"
    }
    $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    $InstalledUvVersion = if ($null -ne $UvCommand) { (& $UvCommand.Source --version) } else { "" }

    if ($ForceUvBootstrap -or -not $InstalledUvVersion.StartsWith("uv $UvVersion")) {
        $UvPath = Join-Path $UvInstallDir "uv.exe"
        if ($ForceUvBootstrap -or -not (Test-Path $UvPath)) {
            Write-Host "[LampGo] 正在安装已验证的 uv $UvVersion..."
            $PreviousUnmanagedInstall = $env:UV_UNMANAGED_INSTALL
            $env:UV_UNMANAGED_INSTALL = $UvInstallDir
            Invoke-RestMethod "https://astral.sh/uv/$UvVersion/install.ps1" | Invoke-Expression
            $env:UV_UNMANAGED_INSTALL = $PreviousUnmanagedInstall
        }
    } else {
        $UvPath = $UvCommand.Source
    }

    if ([string]::IsNullOrWhiteSpace($UvPath)) {
        throw "uv 已执行安装但仍无法定位，请重新打开 PowerShell 后重试。"
    }

    $env:LAMPGO_UV = $UvPath
    & $UvPath run --no-project --python 3.12 (Join-Path $ProjectDir "tools\install_lampgo.py") @args
    $InstallStatus = $LASTEXITCODE
    if ($InstallStatus -ne 0) {
        [Console]::Error.WriteLine("[LampGo] 安装未完成。启动与 uv 引导日志：$BootstrapLog")
    }
} catch {
    [Console]::Error.WriteLine("[LampGo] $($_.Exception.Message)")
    [Console]::Error.WriteLine("[LampGo] 安装未完成。启动与 uv 引导日志：$BootstrapLog")
    $InstallStatus = 1
} finally {
    Stop-Transcript | Out-Null
}

exit $InstallStatus
