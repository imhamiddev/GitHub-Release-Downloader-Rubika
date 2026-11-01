import pytest

from errors import InvalidInput
from github.repository import parse_repo_ref


@pytest.mark.parametrize(
    "text",
    [
        "2dust/v2rayNG",
        "https://github.com/2dust/v2rayNG",
        "http://github.com/2dust/v2rayNG/",
        "https://www.github.com/2dust/v2rayNG/releases/tag/1.0",
        "github.com/2dust/v2rayNG",
        "https://github.com/2dust/v2rayNG.git",
        "<https://github.com/2dust/v2rayNG>",
    ],
)
def test_valid_forms_normalise(text):
    ref = parse_repo_ref(text)
    assert ref.full_name == "2dust/v2rayNG"
    assert ref.url == "https://github.com/2dust/v2rayNG"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "justone",
        "a/b/c",
        "https://evil.com/2dust/v2rayNG",
        "https://github.com.evil.com/a/b",
        "https://user:pw@github.com/a/b",
        "https://github.com/onlyowner",
        "ftp://github.com/a/b",
        "../etc/passwd",
        "a/..",
        "-bad/repo",
        "a/b c",
        "orgs/foo",
    ],
)
def test_invalid_forms_rejected(text):
    with pytest.raises(InvalidInput):
        parse_repo_ref(text)
