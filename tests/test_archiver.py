from src.archiver import save_digest_archive


def test_save_digest_archive_creates_date_file(tmp_path):
    archive_path = save_digest_archive("<html>ok</html>", tmp_path, "Asia/Shanghai")

    assert archive_path.name.endswith(".html")
    assert archive_path.read_text(encoding="utf-8") == "<html>ok</html>"


def test_save_digest_archive_does_not_overwrite_existing_file(tmp_path):
    first = save_digest_archive("first", tmp_path, "Asia/Shanghai")
    second = save_digest_archive("second", tmp_path, "Asia/Shanghai")

    assert first != second
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"
