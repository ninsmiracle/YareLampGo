#!/usr/bin/env bash
set -euo pipefail

bootstrap_log_dir="${LAMPGO_INSTALL_LOG_DIR:-$HOME/.lampgo/logs}"
mkdir -p "$bootstrap_log_dir"
bootstrap_log="$bootstrap_log_dir/bootstrap-$(date +%Y%m%d-%H%M%S)-$$.log"
exec > >(tee -a "$bootstrap_log") 2>&1
echo "[LampGo] 启动日志：$bootstrap_log"

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
uv_bin=$(command -v uv 2>/dev/null || true)
uv_version="${LAMPGO_UV_VERSION:-0.11.29}"
force_uv_bootstrap="${LAMPGO_FORCE_UV_BOOTSTRAP:-0}"
uv_install_dir="${LAMPGO_UV_INSTALL_DIR:-$HOME/.lampgo/tools/uv-$uv_version}"
uv_installed_version=""
if [ -n "$uv_bin" ]; then
    uv_installed_version=$($uv_bin --version 2>/dev/null || true)
fi

if [ "$force_uv_bootstrap" = "1" ] || [[ "$uv_installed_version" != "uv $uv_version"* ]]; then
    uv_bin="$uv_install_dir/uv"
    if [ "$force_uv_bootstrap" = "1" ] || [ ! -x "$uv_bin" ]; then
        echo "[LampGo] 正在安装已验证的 uv $uv_version..."
        uv_installer_url="https://astral.sh/uv/$uv_version/install.sh"
        export UV_UNMANAGED_INSTALL="$uv_install_dir"
        if command -v curl >/dev/null 2>&1; then
            curl -LsSf "$uv_installer_url" | sh
        elif command -v wget >/dev/null 2>&1; then
            wget -qO- "$uv_installer_url" | sh
        else
            echo "[LampGo] 安装失败：需要 curl 或 wget 来下载 uv。" >&2
            exit 1
        fi
        unset UV_UNMANAGED_INSTALL
    fi
fi

if [ -z "$uv_bin" ] || [ ! -x "$uv_bin" ]; then
    echo "[LampGo] uv 已执行安装但仍无法定位，请重新打开终端后重试。" >&2
    exit 1
fi

LAMPGO_UV=$uv_bin
export LAMPGO_UV
set +e
"$uv_bin" run --no-project --python 3.12 "$project_dir/tools/install_lampgo.py" "$@"
install_status=$?
set -e
if [ "$install_status" -ne 0 ]; then
    echo "[LampGo] 安装未完成。启动与 uv 引导日志：$bootstrap_log" >&2
fi
exit "$install_status"
