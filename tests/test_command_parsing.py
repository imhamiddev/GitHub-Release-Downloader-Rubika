from handlers.command_handler import COMMANDS, parse_command


def test_parse_basic():
    assert parse_command("/get 2dust/v2rayNG") == ("get", ["2dust/v2rayNG"])
    assert parse_command("  /GET   a/b  ") == ("get", ["a/b"])
    assert parse_command("/asset 1 3") == ("asset", ["1", "3"])
    assert parse_command("/cancel") == ("cancel", [])


def test_non_commands():
    assert parse_command("hello") is None
    assert parse_command("") is None
    assert parse_command("/") is None
    assert parse_command("/" + "x" * 700) is None
    assert parse_command("/ge$t") is None


def test_suffix_stripped():
    assert parse_command("/help@something") == ("help", [])


def test_all_documented_commands_exist():
    for name in ["start", "get", "latest", "releases", "release", "asset", "downloads", "cancel", "status", "help",
                 "page", "back", "open", "download"]:
        assert name in COMMANDS
