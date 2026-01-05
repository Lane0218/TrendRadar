# coding=utf-8
"""
频率词配置加载模块

负责从配置文件加载频率词规则，支持：
- 普通词组
- 必须词（+前缀）
- 过滤词（!前缀）
- 全局过滤词（[GLOBAL_FILTER] 区域）
- 最大显示数量（@前缀）
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def load_frequency_words(
    frequency_file: Optional[str] = None,
) -> Tuple[List[Dict], List[str], List[str]]:
    """
    加载频率词配置

    配置文件格式说明：
    - 每个词组由空行分隔
    - [GLOBAL_FILTER] 区域定义全局过滤词
    - [WORD_GROUPS] 区域定义词组（默认）

    词组语法：
    - 普通词：直接写入，任意匹配即可
    - +词：必须词，所有必须词都要匹配
    - !词：过滤词，匹配则排除
    - @数字：该词组最多显示的条数

    Args:
        frequency_file: 频率词配置文件路径，默认从环境变量 FREQUENCY_WORDS_PATH 获取或使用 config/frequency_words.txt

    Returns:
        (词组列表, 词组内过滤词, 全局过滤词)

    Raises:
        FileNotFoundError: 频率词文件不存在
    """
    if frequency_file is None:
        frequency_file = os.environ.get(
            "FREQUENCY_WORDS_PATH", "config/frequency_words.txt"
        )

    frequency_path = Path(frequency_file)
    if not frequency_path.exists():
        raise FileNotFoundError(f"频率词文件 {frequency_file} 不存在")

    with open(frequency_path, "r", encoding="utf-8") as f:
        content = f.read()

    word_groups = [group.strip() for group in content.split("\n\n") if group.strip()]

    processed_groups = []
    filter_words = []
    global_filters = []

    # 默认区域（向后兼容）
    current_section = "WORD_GROUPS"

    for group in word_groups:
        lines = [line.strip() for line in group.split("\n") if line.strip()]

        if not lines:
            continue

        # 检查是否为区域标记
        if lines[0].startswith("[") and lines[0].endswith("]"):
            section_name = lines[0][1:-1].upper()
            if section_name in ("GLOBAL_FILTER", "WORD_GROUPS"):
                current_section = section_name
                lines = lines[1:]  # 移除标记行

        # 处理全局过滤区域
        if current_section == "GLOBAL_FILTER":
            # 直接添加所有非空行到全局过滤列表
            for line in lines:
                # 忽略特殊语法前缀，只提取纯文本
                if line.startswith(("!", "+", "@")):
                    continue  # 全局过滤区不支持特殊语法
                if line:
                    global_filters.append(line)
            continue

        # 处理词组区域
        words = lines

        group_required_words = []
        group_normal_words = []
        group_filter_words = []
        group_max_count = 0  # 默认不限制

        for word in words:
            if word.startswith("@"):
                # 解析最大显示数量（只接受正整数）
                try:
                    count = int(word[1:])
                    if count > 0:
                        group_max_count = count
                except (ValueError, IndexError):
                    pass  # 忽略无效的@数字格式
            elif word.startswith("!"):
                filter_words.append(word[1:])
                group_filter_words.append(word[1:])
            elif word.startswith("+"):
                group_required_words.append(word[1:])
            else:
                group_normal_words.append(word)

        if group_required_words or group_normal_words:
            if group_normal_words:
                group_key = " ".join(group_normal_words)
            else:
                group_key = " ".join(group_required_words)

            processed_groups.append(
                {
                    "required": group_required_words,
                    "normal": group_normal_words,
                    "group_key": group_key,
                    "max_count": group_max_count,
                }
            )

    return processed_groups, filter_words, global_filters


def _should_use_ascii_word_boundary(keyword: str) -> bool:
    """
    判断一个关键词是否应使用 ASCII 单词边界匹配。

    目的：避免短英文关键词（如 AI/EU/G7）在英文单词内部误命中，
    例如 "arrAIgned"、"Air"、"sAid" 等。

    规则：仅对 2~4 位的纯 ASCII 字母/数字关键词启用。
    """
    if not keyword:
        return False
    if not isinstance(keyword, str):
        keyword = str(keyword)
    if len(keyword) < 2 or len(keyword) > 4:
        return False
    # 只针对纯 ASCII 字母/数字短词启用边界匹配（中英文混合/带符号短词仍按子串匹配）
    return keyword.isascii() and keyword.isalnum()


@lru_cache(maxsize=4096)
def _compile_ascii_boundary_pattern(keyword_lower: str) -> re.Pattern:
    """
    编译短英文关键词的 ASCII 边界匹配正则。

    使用 ASCII 字母/数字边界，而不是 \\b：
    - \\b 在 Unicode 下会把中文也视为 "word char"，导致 "AI新能源" 不匹配
    - ASCII 边界则可在 "AI新能源" / "GPU计算" 等中文紧邻场景匹配，同时避免在英文单词内部误命中
    """
    escaped = re.escape(keyword_lower)
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def _keyword_in_title(title: str, title_lower: str, keyword: str) -> bool:
    """判断 keyword 是否匹配 title（支持短英文 ASCII 边界匹配）。"""
    if not keyword:
        return False
    if not isinstance(keyword, str):
        keyword = str(keyword)
    if not keyword:
        return False

    if _should_use_ascii_word_boundary(keyword):
        pattern = _compile_ascii_boundary_pattern(keyword.lower())
        return bool(pattern.search(title))

    return keyword.lower() in title_lower


def matches_group(title: str, group: Dict) -> bool:
    """
    判断标题是否匹配单个词组（不包含全局过滤/过滤词逻辑）。

    说明：该函数用于在已通过过滤检查后，确定命中的是哪个词组，
    保证与 matches_word_groups 的匹配规则一致。
    """
    # 防御性类型检查：确保 title 是有效字符串
    if not isinstance(title, str):
        title = str(title) if title is not None else ""
    if not title:
        return False

    title_lower = title.lower()
    required_words = group.get("required", []) or []
    normal_words = group.get("normal", []) or []

    # 必须词：全部命中
    if required_words:
        if not all(_keyword_in_title(title, title_lower, w) for w in required_words):
            return False

    # 普通词：任意命中
    if normal_words:
        if not any(_keyword_in_title(title, title_lower, w) for w in normal_words):
            return False

    return True


def matches_word_groups(
    title: str,
    word_groups: List[Dict],
    filter_words: List[str],
    global_filters: Optional[List[str]] = None
) -> bool:
    """
    检查标题是否匹配词组规则

    Args:
        title: 标题文本
        word_groups: 词组列表
        filter_words: 过滤词列表
        global_filters: 全局过滤词列表

    Returns:
        是否匹配
    """
    # 防御性类型检查：确保 title 是有效字符串
    if not isinstance(title, str):
        title = str(title) if title is not None else ""
    if not title.strip():
        return False

    title_lower = title.lower()

    # 全局过滤检查（优先级最高）
    if global_filters:
        if any(_keyword_in_title(title, title_lower, global_word) for global_word in global_filters):
            return False

    # 如果没有配置词组，则匹配所有标题（支持显示全部新闻）
    if not word_groups:
        return True

    # 过滤词检查
    if any(_keyword_in_title(title, title_lower, filter_word) for filter_word in filter_words):
        return False

    # 词组匹配检查
    for group in word_groups:
        if matches_group(title, group):
            return True

    return False
