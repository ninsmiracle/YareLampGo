from pathlib import Path

from lampgo.context.codex_memory import CodexMemorySummaryProvider


def test_codex_memory_reads_only_summary_and_selects_relevant_sections(tmp_path: Path) -> None:
    summary = tmp_path / "memory_summary.md"
    summary.write_text(
        "## User Profile\n- 喜欢简洁\n\n"
        "## LampGo\n- LampGo 使用本机 Codex 执行复杂任务\n\n"
        "## Cooking\n- 喜欢意大利面\n",
        encoding="utf-8",
    )
    provider = CodexMemorySummaryProvider(summary)

    context = provider.get_context("LampGo Codex", max_chars=90)

    assert "LampGo 使用本机 Codex" in context
    assert len(context) <= 90


def test_codex_memory_cache_refreshes_on_mtime_change(tmp_path: Path) -> None:
    summary = tmp_path / "memory_summary.md"
    summary.write_text("## User Profile\n- 旧称呼\n", encoding="utf-8")
    provider = CodexMemorySummaryProvider(summary)
    assert "旧称呼" in provider.get_context(max_chars=200)

    summary.write_text("## User Profile\n- 新称呼和更多内容\n", encoding="utf-8")
    assert "新称呼" in provider.get_context(max_chars=200)


def test_codex_memory_ranks_chinese_query_by_bigrams(tmp_path: Path) -> None:
    summary = tmp_path / "memory_summary.md"
    summary.write_text(
        "## Cooking\n- 喜欢意大利面和番茄\n\n"
        "## LampGo\n- 台灯表情应该活泼有趣\n",
        encoding="utf-8",
    )
    provider = CodexMemorySummaryProvider(summary)

    context = provider.get_context("帮我调整台灯表情", max_chars=22)

    assert "台灯表情" in context


def test_missing_codex_memory_is_non_blocking(tmp_path: Path) -> None:
    provider = CodexMemorySummaryProvider(tmp_path / "missing.md")
    assert provider.get_context("anything") == ""
