#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_dir="$project_dir/skills/lampgo-setup"
codex_root="${CODEX_HOME:-$HOME/.codex}"
skills_dir="$codex_root/skills"
target_dir="$skills_dir/lampgo-setup"

if [ ! -f "$source_dir/SKILL.md" ]; then
    echo "[LampGo] 找不到 skill 源文件：$source_dir/SKILL.md" >&2
    exit 1
fi

mkdir -p "$skills_dir"

if [ -L "$target_dir" ]; then
    current_target=$(readlink "$target_dir" || true)
    if [ "$current_target" = "$source_dir" ]; then
        echo "[LampGo] Codex skill 已安装：$target_dir"
        exit 0
    fi
    echo "[LampGo] 安装目标已是其他符号链接：$target_dir -> $current_target" >&2
    echo "[LampGo] 为保护已有配置，未覆盖。请先检查或移动该链接后重试。" >&2
    exit 1
elif [ -e "$target_dir" ]; then
    echo "[LampGo] 安装目标已存在且不是符号链接：$target_dir" >&2
    echo "[LampGo] 为保护已有内容，未覆盖。请先检查或移动该目录后重试。" >&2
    exit 1
fi

ln -s "$source_dir" "$target_dir"
echo "[LampGo] 已安装 Codex skill：$target_dir -> $source_dir"
echo "[LampGo] 请新建一个 Codex 任务，然后说：用 \$lampgo-setup 帮我安装和配置 YareLampGo V2.0。"
