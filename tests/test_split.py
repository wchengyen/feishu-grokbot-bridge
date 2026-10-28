from bridge.textutil import delivery_plan, split_text


def test_short_text_is_one_chunk() -> None:
    assert split_text("hello", 4000) == ["hello"]
    assert split_text("", 4000) == []


def test_long_text_splits_on_newline_under_the_limit() -> None:
    block = "段落" * 100  # 200 chars
    text = "\n".join([block] * 30)  # well over 4000
    chunks = split_text(text, 4000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_hard_cut_when_there_is_no_break() -> None:
    text = "字" * 50
    chunks = split_text(text, 20)
    assert chunks == ["字" * 20, "字" * 20, "字" * 10]


def test_very_long_text_uses_a_file() -> None:
    text = "a" * 12001
    mode, chunks = delivery_plan(text, split_at=4000, file_at=12000)
    assert mode == "file"
    assert len(chunks) == 1
    assert "全文已上傳為檔案" in chunks[0]
    assert len(chunks[0]) < 12000


def test_medium_text_stays_messages() -> None:
    text = "a\n" * 3000
    mode, chunks = delivery_plan(text, split_at=4000, file_at=12000)
    assert mode == "messages"
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
