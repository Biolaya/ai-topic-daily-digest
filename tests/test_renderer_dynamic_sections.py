from datetime import date

from src.renderer import make_subject, render_email


def test_make_subject_includes_dynamic_topic_count():
    assert make_subject(current_date=date(2026, 5, 23), topic_count=3) == "【每日简报】今日 3 个主题更新 - 2026-05-23"


def test_render_email_contains_dynamic_sections_and_clickable_links():
    digest = {
        "overview": [
            "AI 模型发布继续活跃。",
            "Minecraft 更新受到玩家关注。",
        ],
        "sections": [
            {
                "topic": "AI",
                "topic_summary": "AI 今日有模型产品更新。",
                "news_items": [
                    {
                        "title": "OpenAI 发布新模型",
                        "source": "Example AI",
                        "published_at": "2026-05-23",
                        "summary": "新模型面向开发者开放，原文给出了能力变化和发布时间。",
                        "url": "https://example.com/ai",
                    }
                ],
            },
            {
                "topic": "Minecraft",
                "topic_summary": "Minecraft 今日有版本更新。",
                "news_items": [
                    {
                        "title": "Minecraft 发布测试版",
                        "source": "Example Game",
                        "published_at": "2026-05-23",
                        "summary": "测试版调整了方块和生物行为。",
                        "url": "https://example.com/minecraft",
                    }
                ],
            },
        ],
        "top5": [
            {
                "topic": "AI",
                "title": "OpenAI 发布新模型",
                "reason": "影响开发者模型选择。",
                "url": "https://example.com/ai",
            }
        ],
    }

    html = render_email(digest, "【每日简报】今日 2 个主题更新 - 2026-05-23")

    assert "总体摘要看点" in html
    assert "一、AI 今日重点" in html
    assert "二、Minecraft 今日重点" in html
    assert "今日最值得关注的 5 条" in html
    assert 'href="https://example.com/ai"' in html
    assert 'href="https://example.com/minecraft"' in html


def test_render_email_handles_topic_with_no_news():
    digest = {
        "overview": ["今日暂无高质量更新。"],
        "sections": [
            {
                "topic": "Anime",
                "topic_summary": "今日暂无高质量更新。",
                "news_items": [],
            }
        ],
        "top5": [],
    }

    html = render_email(digest, "【每日简报】今日 1 个主题更新 - 2026-05-23")

    assert "一、Anime 今日重点" in html
    assert "今日暂无高质量更新。" in html
