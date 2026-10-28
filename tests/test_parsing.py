from bridge.parsing import (
    bot_is_mentioned,
    extract_text,
    parse_command,
    parse_event,
    strip_mentions,
)
from tests.events import make_event


def test_text_message_strips_mention_placeholder() -> None:
    mentions = [
        {
            "key": "@_user_1",
            "id": {"open_id": "ou_bot", "user_id": "", "union_id": ""},
            "name": "Javis",
            "tenant_key": "t",
        }
    ]
    event = make_event(
        chat_type="group",
        text="@_user_1  幫我整理  會議記錄",
        mentions=mentions,
    )
    parsed = parse_event(event, bot_open_id="ou_bot")
    assert parsed is not None
    assert parsed.text == "幫我整理 會議記錄"
    assert parsed.mentioned_bot is True
    assert parsed.command is None


def test_post_rich_text_flattens_and_skips_at_tags() -> None:
    content = {
        "title": "標題",
        "content": [
            [
                {"tag": "text", "text": "請"},
                {"tag": "at", "user_id": "@_user_1", "user_name": "Javis"},
                {"tag": "text", "text": " 總結"},
            ],
            [{"tag": "a", "text": "文件", "href": "https://example.com/a"}],
            [{"tag": "img", "image_key": "img"}],
        ],
    }
    event = make_event(message_type="post", content=content, text="")
    parsed = parse_event(event, bot_open_id="")
    assert parsed is not None
    assert parsed.text == "標題\n請 總結\n文件\n[圖片]"


def test_strip_mentions_keeps_newlines() -> None:
    assert strip_mentions("a @_user_1\nb  @_all", ["@_user_1"]) == "a\nb"


def test_all_mention_alone_is_not_the_bot() -> None:
    mention = type("M", (), {"key": "@_all", "id": type("I", (), {"open_id": ""})()})()
    assert bot_is_mentioned([mention], "") is False
    assert bot_is_mentioned([mention], "ou_bot") is False


def test_bot_open_id_must_match_when_configured() -> None:
    other = type("M", (), {"key": "@_user_1", "id": type("I", (), {"open_id": "ou_other"})()})()
    bot = type("M", (), {"key": "@_user_2", "id": type("I", (), {"open_id": "ou_bot"})()})()
    assert bot_is_mentioned([other], "ou_bot") is False
    assert bot_is_mentioned([bot], "ou_bot") is True


def test_commands_are_case_insensitive() -> None:
    assert parse_command("  /Ping  ") == "/ping"
    assert parse_command("/help me") == "/help"
    assert parse_command("not a command") is None


def test_unsupported_type_has_empty_text() -> None:
    assert extract_text("image", {"image_key": "img"}, []) == ""


def test_malformed_content_does_not_raise() -> None:
    event = make_event(content="{not-json")
    parsed = parse_event(event)
    assert parsed is not None
    assert parsed.text == ""
