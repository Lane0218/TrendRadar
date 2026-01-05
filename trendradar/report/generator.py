# coding=utf-8
"""
报告生成模块

提供报告数据准备和 HTML 生成功能：
- prepare_report_data: 准备报告数据
- generate_html_report: 生成 HTML 报告
"""

from pathlib import Path
from typing import Dict, List, Optional, Callable


PERSONAL_RSS_GROUP_KEY = "个人博客更新"


def prepare_report_data(
    stats: List[Dict],
    failed_ids: Optional[List] = None,
    new_titles: Optional[Dict] = None,
    id_to_name: Optional[Dict] = None,
    mode: str = "daily",
    rank_threshold: int = 3,
    matches_word_groups_func: Optional[Callable] = None,
    load_frequency_words_func: Optional[Callable] = None,
    rss_stats: Optional[List[Dict]] = None,
    rss_new_stats: Optional[List[Dict]] = None,
) -> Dict:
    """
    准备报告数据

    Args:
        stats: 统计结果列表
        failed_ids: 失败的 ID 列表
        new_titles: 新增标题
        id_to_name: ID 到名称的映射
        mode: 报告模式 (daily/incremental/current)
        rank_threshold: 排名阈值
        matches_word_groups_func: 词组匹配函数
        load_frequency_words_func: 加载频率词函数

    Returns:
        Dict: 准备好的报告数据
    """
    # -----------------------------------------------------------
    # 展示策略（面向个人使用）：
    # - 关键词筛选内容：热榜 + RSS（新闻/GitHub 等）合并到同一套 stats 里展示
    # - 个人博客等 “filter_by_keywords=false” 的 RSS：不参与关键词合并，单独展示
    # -----------------------------------------------------------
    rss_stats = rss_stats or []
    rss_new_stats = rss_new_stats or []

    personal_rss_stats = [s for s in rss_stats if s.get("word") == PERSONAL_RSS_GROUP_KEY]
    rss_keyword_stats = [s for s in rss_stats if s.get("word") != PERSONAL_RSS_GROUP_KEY]

    personal_rss_new_stats = [s for s in rss_new_stats if s.get("word") == PERSONAL_RSS_GROUP_KEY]
    rss_keyword_new_stats = [s for s in rss_new_stats if s.get("word") != PERSONAL_RSS_GROUP_KEY]

    # 将 RSS 的关键词统计合并进热榜 stats（按 group_key/word 合并）
    if rss_keyword_stats:
        merged_by_word: Dict[str, Dict] = {}

        for stat in stats or []:
            word = stat.get("word")
            if not word:
                continue
            merged_by_word[word] = dict(stat)

        for rss_stat in rss_keyword_stats:
            word = rss_stat.get("word")
            if not word:
                continue

            if word in merged_by_word:
                merged = merged_by_word[word]
                merged["count"] = int(merged.get("count", 0) or 0) + int(rss_stat.get("count", 0) or 0)
                merged["titles"] = list(merged.get("titles", []) or []) + list(rss_stat.get("titles", []) or [])

                # position 用于排序稳定（存在时取更靠前的）
                merged_position = merged.get("position", 999)
                rss_position = rss_stat.get("position", 999)
                try:
                    merged["position"] = min(int(merged_position), int(rss_position))
                except (TypeError, ValueError):
                    merged["position"] = merged_position

                # percentage 在 HTML 中不使用，简单相加即可（避免除法/总数语义纠结）
                try:
                    merged["percentage"] = float(merged.get("percentage", 0) or 0) + float(rss_stat.get("percentage", 0) or 0)
                except (TypeError, ValueError):
                    pass
            else:
                merged_by_word[word] = dict(rss_stat)

        stats = list(merged_by_word.values())
        stats.sort(
            key=lambda x: (
                -int(x.get("count", 0) or 0),
                int(x.get("position", 999) or 999),
            )
        )

    # 将“个人博客更新”组展开为“按 feed 分组”的 stats，方便 HTML 直接渲染
    personal_feed_stats: List[Dict] = []
    if personal_rss_stats:
        # 汇总所有个人 RSS 条目（通常只有 1 组，但兼容多组）
        all_personal_titles: List[Dict] = []
        for stat in personal_rss_stats:
            all_personal_titles.extend(list(stat.get("titles", []) or []))

        titles_by_feed: Dict[str, List[Dict]] = {}
        for title_data in all_personal_titles:
            feed_name = title_data.get("source_name", "") or "RSS"
            titles_by_feed.setdefault(feed_name, []).append(title_data)

        def _rank_key(item: Dict) -> int:
            ranks = item.get("ranks", [])
            if not ranks:
                return 999
            try:
                return int(min(ranks))
            except (TypeError, ValueError):
                return 999

        # 先按每个 feed 内部排序（最新在前：rank 越小越新）
        feed_summaries = []
        for feed_name, items in titles_by_feed.items():
            sorted_items = sorted(items, key=_rank_key)

            # 避免标题里重复显示 feed 名称：group header 已有
            cleaned_items = []
            for item in sorted_items:
                item_copy = dict(item)
                item_copy["source_name"] = ""
                item_copy["ranks"] = []  # 个人博客不展示“排名”
                cleaned_items.append(item_copy)

            latest_rank = _rank_key(sorted_items[0]) if sorted_items else 999
            feed_summaries.append((feed_name, cleaned_items, latest_rank))

        # feed 排序：先看“最近更新”，再按条目数
        feed_summaries.sort(key=lambda x: (x[2], -len(x[1]), x[0]))

        for pos, (feed_name, items, _) in enumerate(feed_summaries):
            personal_feed_stats.append(
                {
                    "word": feed_name,
                    "count": len(items),
                    "position": pos,
                    "titles": items,
                    "percentage": 0,
                }
            )

    processed_new_titles = []

    # 在增量模式下隐藏新增新闻区域
    hide_new_section = mode == "incremental"

    # 只有在非隐藏模式下才处理新增新闻部分
    if not hide_new_section:
        filtered_new_titles = {}
        if new_titles and id_to_name:
            # 如果提供了匹配函数，使用它过滤
            if matches_word_groups_func and load_frequency_words_func:
                word_groups, filter_words, global_filters = load_frequency_words_func()
                for source_id, titles_data in new_titles.items():
                    filtered_titles = {}
                    for title, title_data in titles_data.items():
                        if matches_word_groups_func(title, word_groups, filter_words, global_filters):
                            filtered_titles[title] = title_data
                    if filtered_titles:
                        filtered_new_titles[source_id] = filtered_titles
            else:
                # 没有匹配函数时，使用全部
                filtered_new_titles = new_titles

            # 打印过滤后的新增热点数（与推送显示一致）
            original_new_count = sum(len(titles) for titles in new_titles.values()) if new_titles else 0
            filtered_new_count = sum(len(titles) for titles in filtered_new_titles.values()) if filtered_new_titles else 0
            if original_new_count > 0:
                print(f"频率词过滤后：{filtered_new_count} 条新增热点匹配（原始 {original_new_count} 条）")

        if filtered_new_titles and id_to_name:
            for source_id, titles_data in filtered_new_titles.items():
                source_name = id_to_name.get(source_id, source_id)
                source_titles = []

                for title, title_data in titles_data.items():
                    url = title_data.get("url", "")
                    mobile_url = title_data.get("mobileUrl", "")
                    ranks = title_data.get("ranks", [])

                    processed_title = {
                        "title": title,
                        "source_name": source_name,
                        "time_display": "",
                        "count": 1,
                        "ranks": ranks,
                        "rank_threshold": rank_threshold,
                        "url": url,
                        "mobile_url": mobile_url,
                        "is_new": True,
                    }
                    source_titles.append(processed_title)

                if source_titles:
                    processed_new_titles.append(
                        {
                            "source_id": source_id,
                            "source_name": source_name,
                            "titles": source_titles,
                        }
                    )

    processed_stats = []
    for stat in stats:
        if stat["count"] <= 0:
            continue

        processed_titles = []
        for title_data in stat["titles"]:
            processed_title = {
                "title": title_data["title"],
                "source_name": title_data["source_name"],
                "time_display": title_data["time_display"],
                "count": title_data["count"],
                "ranks": title_data["ranks"],
                "rank_threshold": title_data["rank_threshold"],
                "url": title_data.get("url", ""),
                "mobile_url": title_data.get("mobile_url") or title_data.get("mobileUrl", ""),
                "is_new": title_data.get("is_new", False),
            }
            processed_titles.append(processed_title)

        processed_stats.append(
            {
                "word": stat["word"],
                "count": stat["count"],
                "percentage": stat.get("percentage", 0),
                "titles": processed_titles,
            }
        )

    return {
        "stats": processed_stats,
        "new_titles": processed_new_titles,
        # RSS 统计（可选）：用于邮件/HTML 报告合并展示
        # rss_stats/rss_new_stats 的结构与 stats 一致：
        # [{"word": "...", "count": N, "titles": [...]}]
        # 关键词筛选的 RSS 已合并进 stats；这里只保留“个人博客更新”等不做关键词筛选的 RSS（按 feed 分组）。
        "rss_stats": personal_feed_stats,
        "rss_new_stats": [],
        "failed_ids": failed_ids or [],
        "total_new_count": sum(
            len(source["titles"]) for source in processed_new_titles
        ),
    }


def generate_html_report(
    stats: List[Dict],
    total_titles: int,
    failed_ids: Optional[List] = None,
    new_titles: Optional[Dict] = None,
    id_to_name: Optional[Dict] = None,
    mode: str = "daily",
    is_daily_summary: bool = False,
    update_info: Optional[Dict] = None,
    rank_threshold: int = 3,
    output_dir: str = "output",
    date_folder: str = "",
    time_filename: str = "",
    render_html_func: Optional[Callable] = None,
    matches_word_groups_func: Optional[Callable] = None,
    load_frequency_words_func: Optional[Callable] = None,
    enable_index_copy: bool = True,
    rss_stats: Optional[List[Dict]] = None,
    rss_new_stats: Optional[List[Dict]] = None,
) -> str:
    """
    生成 HTML 报告

    Args:
        stats: 统计结果列表
        total_titles: 总标题数
        failed_ids: 失败的 ID 列表
        new_titles: 新增标题
        id_to_name: ID 到名称的映射
        mode: 报告模式 (daily/incremental/current)
        is_daily_summary: 是否是每日汇总
        update_info: 更新信息
        rank_threshold: 排名阈值
        output_dir: 输出目录
        date_folder: 日期文件夹名称
        time_filename: 时间文件名
        render_html_func: HTML 渲染函数
        matches_word_groups_func: 词组匹配函数
        load_frequency_words_func: 加载频率词函数
        enable_index_copy: 是否复制到 index.html

    Returns:
        str: 生成的 HTML 文件路径
    """
    if is_daily_summary:
        if mode == "current":
            filename = "当前榜单汇总.html"
        elif mode == "incremental":
            filename = "当日增量.html"
        else:
            filename = "当日汇总.html"
    else:
        filename = f"{time_filename}.html"

    # 构建输出路径
    output_path = Path(output_dir) / date_folder / "html"
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = str(output_path / filename)

    # 准备报告数据
    report_data = prepare_report_data(
        stats,
        failed_ids,
        new_titles,
        id_to_name,
        mode,
        rank_threshold,
        matches_word_groups_func,
        load_frequency_words_func,
        rss_stats=rss_stats,
        rss_new_stats=rss_new_stats,
    )

    # 渲染 HTML 内容
    if render_html_func:
        html_content = render_html_func(
            report_data, total_titles, is_daily_summary, mode, update_info
        )
    else:
        # 默认简单 HTML
        html_content = f"<html><body><h1>Report</h1><pre>{report_data}</pre></body></html>"

    # 写入文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 如果是每日汇总且启用 index 复制
    if is_daily_summary and enable_index_copy:
        # 生成到根目录（供 GitHub Pages 访问）
        root_index_path = Path("index.html")
        with open(root_index_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # 同时生成到 output 目录（供 Docker Volume 挂载访问）
        output_index_path = Path(output_dir) / "index.html"
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        with open(output_index_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    return file_path
