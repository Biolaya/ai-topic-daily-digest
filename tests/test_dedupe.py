from src.dedupe import filter_new_items, normalize_url, titles_are_similar


def test_normalize_url_removes_tracking_params():
    url = "HTTPS://Example.com/news/?utm_source=x&id=1#section"
    assert normalize_url(url) == "https://example.com/news?id=1"


def test_filter_new_items_dedupes_by_url():
    items = [
        {"title": "OpenAI news", "url": "https://example.com/a?utm_source=x"},
        {"title": "OpenAI news updated", "url": "https://example.com/a"},
    ]

    assert len(filter_new_items(items)) == 1


def test_filter_new_items_dedupes_similar_titles():
    items = [
        {"title": "OpenAI releases new AI model today", "url": "https://example.com/a"},
        {"title": "OpenAI releases new AI model today!", "url": "https://example.com/b"},
    ]

    assert len(filter_new_items(items)) == 1
    assert titles_are_similar(items[0]["title"], items[1]["title"])


def test_filter_new_items_excludes_sent_urls():
    items = [
        {"title": "Football transfer news", "url": "https://example.com/football"},
        {"title": "Fresh AI news", "url": "https://example.com/ai"},
    ]

    result = filter_new_items(items, is_sent=lambda url: url == "https://example.com/football")

    assert [item["title"] for item in result] == ["Fresh AI news"]
