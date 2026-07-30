#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
codex_root="${CODEX_HOME:-$HOME/.codex}"
skills_dir="$codex_root/skills"
skill_names=("lampgo-setup" "lampgo-control")

mkdir -p "$skills_dir"

for skill_name in "${skill_names[@]}"; do
    source_dir="$project_dir/skills/$skill_name"
    target_dir="$skills_dir/$skill_name"

    if [ ! -f "$source_dir/SKILL.md" ]; then
        echo "[LampGo] 找不到 skill 源文件：$source_dir/SKILL.md" >&2
        exit 1
    fi

    if [ -L "$target_dir" ]; then
        current_target=$(readlink "$target_dir" || true)
        if [ "$current_target" = "$source_dir" ]; then
            echo "[LampGo] Codex skill 已安装：$target_dir"
            continue
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
done

echo "[LampGo] 装机时使用：用 \$lampgo-setup 帮我安装和配置 YareLampGo V2.0。"
echo "[LampGo] 控制时使用：用 \$lampgo-control 控制 LampGo 完成一个真实任务。"
