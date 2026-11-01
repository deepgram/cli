"""dg init clones templates only from Deepgram's GitHub organizations."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_init import lifecycle
from deepctl_cmd_init.command import InitCommand
from deepctl_cmd_init.models import InitResult, TemplateDetail
from deepctl_core import AuthManager, Config, DeepgramClient


def _detail(name: str, url: str) -> TemplateDetail:
    return TemplateDetail(
        name=name,
        title="t",
        description="d",
        language="javascript",
        links={"github": url},
    )


def _handle(tmp_path, monkeypatch, detail: TemplateDetail, slug: str) -> object:
    clone = Mock()
    monkeypatch.setattr(lifecycle, "check_prereqs", lambda: [])
    monkeypatch.setattr(lifecycle, "clone_template", clone)
    auth = Mock(spec=AuthManager)
    auth.get_api_key.return_value = None
    with patch(
        "deepctl_cmd_init.command.templates_api.get_template", return_value=detail
    ):
        result = InitCommand().handle(
            config=Mock(spec=Config),
            auth_manager=auth,
            client=Mock(spec=DeepgramClient),
            template=slug,
            dir=None,
            search=None,
            list=False,
            install=False,
            no_install=True,
            start=False,
            no_start=True,
        )
    clone.assert_not_called()
    return result


class TestTemplateName:
    """The template name comes from the service too: it must be plain."""

    @pytest.mark.parametrize(
        ("name", "slug", "url"),
        [
            ("--yes", "x", "https://github.com/deepgram-starters/x"),
            ("--yes", "--yes", "https://github.com/deepgram-starters/x"),
            ("../x", "../x", "https://github.com/deepgram-starters/x"),
            ("a b", "a b", "https://github.com/deepgram-starters/x"),
        ],
    )
    def test_refused(self, tmp_path, monkeypatch, name, slug, url) -> None:
        monkeypatch.chdir(tmp_path)
        result = _handle(tmp_path, monkeypatch, _detail(name, url), slug)
        assert isinstance(result, InitResult)
        assert result.status == "error"
        assert "isn't plain" in (result.message or "")
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("name", ["node-transcription", "a.b_c-1", "X"])
    def test_valid_names(self, name: str) -> None:
        assert lifecycle.valid_name(name)

    @pytest.mark.parametrize("name", ["", "-x", ".", "..", "a/b", "a b", "\u0435", 1])
    def test_invalid_names(self, name: object) -> None:
        assert not lifecycle.valid_name(name)


class TestRepositoryCheck:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/deepgram/deepgram-js-sdk",
            "https://github.com/deepgram-starters/node-transcription",
            "https://github.com/deepgram-starters/node-transcription/",
            "https://github.com/deepgram-starters/node-transcription.git",
        ],
    )
    def test_accepted(self, url: str) -> None:
        assert lifecycle.is_template_repo(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/deepgram/x",
            "ssh://git@github.com/deepgram/x",
            "git@github.com:deepgram/x.git",
            "https://github.com/deepgramx/x",
            "https://github.com/deepgram-starters-evil/x",
            "https://github.com/someone/deepgram",
            "https://github.com.evil.example/deepgram/x",
            "https://evil.example/github.com/deepgram/x",
            "https://user@github.com/deepgram/x",
            "https://github.com:8443/deepgram/x",
            "https://github.com/deepgram/x?ref=y",
            "https://github.com/deepgram/x#y",
            "https://github.com/deepgram/x/../../evil/y",
            "https://github.com/deepgram/..",
            "https://github.com/deepgram/",
            "https://github.com/deepgram",
            "https://github.com/deepgram/x/tree/main",
            "--upload-pack=touch /tmp/pwned",
            "file:///tmp/repo",
            "https://GITHUB.com/deepgram/x",
            "https://github.com\\@evil.example/deepgram/x",
            "https://github.com/deepgram/x\n--upload-pack=touch /tmp/pwned",
            "https://github.com/deepgram/x\t",
            "https://github.com/deepgram/x y",
            "https://github.com/deepgram/x\x1b[0m",
            "https://github.com/deepgram/%2e%2e",
            "https://github.com/deepgram/..%2fevil%2fx",
            "https://github.com/deepgram/x%2F..%2F..%2Fevil%2Fy",
            "https://github.com/deepgram/x%00",
            "https://github.com/deepgram/-u",
            "https://github.com/deepgram/--upload-pack=touch",
            "https://github.com/deepgram/x;y",
            "https://github.com/deepgram/\u0435vil",
            "",
        ],
    )
    def test_refused(self, url: str) -> None:
        assert not lifecycle.is_template_repo(url)

    def test_refused_repository_is_not_cloned(self, tmp_path, monkeypatch) -> None:
        detail = TemplateDetail(
            name="node-transcription",
            title="Node Transcription",
            description="d",
            language="javascript",
            links={"github": "https://evil.example/deepgram/x"},
        )
        clone = Mock()
        monkeypatch.setattr(lifecycle, "check_prereqs", lambda: [])
        monkeypatch.setattr(lifecycle, "clone_template", clone)
        auth = Mock(spec=AuthManager)
        auth.get_api_key.return_value = None
        with patch(
            "deepctl_cmd_init.command.templates_api.get_template", return_value=detail
        ):
            result = InitCommand().handle(
                config=Mock(spec=Config),
                auth_manager=auth,
                client=Mock(spec=DeepgramClient),
                template="node-transcription",
                dir=str(tmp_path / "app"),
                search=None,
                list=False,
                install=False,
                no_install=True,
                start=False,
                no_start=True,
            )
        assert isinstance(result, InitResult)
        assert result.status == "error"
        assert "github.com/deepgram-starters" in (result.message or "")
        clone.assert_not_called()
        assert not (tmp_path / "app").exists()
