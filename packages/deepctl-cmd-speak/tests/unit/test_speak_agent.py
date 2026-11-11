"""Agent rules for ``dg speak`` under a hard signal, and
the ``next_step`` rows for every speak coded error (``test_next_step``).
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import types
from typing import Any
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_speak import command as speak
from deepctl_cmd_speak import sinks
from deepctl_cmd_speak.command import SpeakCommand
from deepctl_cmd_speak.models import SpeakCodedError
from deepctl_core import AuthManager, Config, DeepgramClient

# The strict surface-0.5.0 schema's next_step grammar.
NEXT_STEP = re.compile(
    r"^(?!sudo )(?:[A-Za-z0-9_@%+:,./-][A-Za-z0-9_@%+=:,./-]*|'[^']*'(?:\"'\"'[^']*')*)"
    r"(?: (?:[A-Za-z0-9_@%+:,./-][A-Za-z0-9_@%+=:,./-]*|'[^']*'(?:\"'\"'[^']*')*))*$"
)
_REAL_TAKES_AUDIO = sinks.stdout_takes_audio
OUT_FILE = re.compile(r"^speech-\d{8}T\d{6}Z-[0-9a-f]{4}\.(mp3|wav|ogg|flac|aac|raw)$")


def _kwargs(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "text": "hi",
        "output": None,
        "out_file": None,
        "force": False,
        "model": "aura-2-asteria-en",
        "encoding": None,
        "container": None,
        "sample_rate": None,
        "file": None,
        "play": False,
        "list_voices": False,
    }
    base.update(kw)
    return base


def _pre(argv: list[str], monkeypatch, **kw: Any):
    monkeypatch.setattr(speak, "_argv", lambda: list(argv))
    return SpeakCommand().pre_auth_check(Mock(spec=Config), **_kwargs(**kw))


def assert_e10(ns: str | None) -> None:
    if ns is None:
        return
    assert shlex.join(shlex.split(ns)) == ns
    assert NEXT_STEP.match(ns), ns
    tokens = shlex.split(ns)
    assert tokens[0] == "dg"
    assert not any(re.fullmatch(r"<[A-Z_]+>", t) for t in tokens)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(speak, "_deprecation_shown", False)
    monkeypatch.setattr(speak, "_agentic", True)
    monkeypatch.setattr(speak, "agent_mode", lambda: True)
    monkeypatch.setattr(sinks, "stdout_stat", lambda: None)  # a pipe
    monkeypatch.setattr(sinks, "stdout_takes_audio", lambda: False)


# ── No text → never read stdin ─────────────────────────────────────────


class TestNoText:
    @pytest.mark.parametrize("extra", [{}, {"out_file": "a.wav"}, {"text": ""}])
    def test_refused_without_reading_stdin(self, monkeypatch, extra):
        with patch("deepctl_cmd_speak.command.sys") as mock_sys:
            kw = {"text": None, **extra}
            result = _pre(["speak"], monkeypatch, **kw)
            mock_sys.stdin.read.assert_not_called()
        assert isinstance(result, SpeakCodedError)
        assert result.code == "invalid_argument"
        assert result.error == (
            "dg speak needs the text as an argument, or - to read it from stdin."
        )
        assert result.hint == (
            "Pass the text in quotes. To read it from stdin, pass - explicitly."
        )
        assert result.next_step == "dg speak --help"

    def test_file_is_text(self, monkeypatch, tmp_path):
        assert (
            _pre(["speak"], monkeypatch, text=None, file="t.txt", out_file="a.wav")
            is None
        )

    def test_dash_reads_stdin_in_agent_mode(self, monkeypatch):
        monkeypatch.setattr(sinks, "stdout_takes_audio", lambda: True)
        assert _pre(["speak", "-"], monkeypatch, text="-") is None
        cmd = SpeakCommand()
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = iter([b"a"])
        with patch("deepctl_cmd_speak.command.sys") as mock_sys:
            mock_sys.stdin.read.return_value = "from stdin\n"
            mock_sys.stdout.isatty.return_value = False
            cmd.handle(
                Mock(spec=Config), Mock(spec=AuthManager), client, **_kwargs(text="-")
            )
        assert client.speak_text.call_args.kwargs["text"] == "from stdin"

    def test_dash_reads_stdin_for_a_person_too(self, monkeypatch):
        monkeypatch.setattr(speak, "agent_mode", lambda: False)
        cmd = SpeakCommand()
        client = Mock(spec=DeepgramClient)
        client.speak_text.return_value = iter([b"a"])
        with patch("deepctl_cmd_speak.command.sys") as mock_sys:
            mock_sys.stdin.isatty.return_value = True
            mock_sys.stdin.read.return_value = "typed"
            mock_sys.stdout.isatty.return_value = False
            cmd.handle(
                Mock(spec=Config), Mock(spec=AuthManager), client, **_kwargs(text="-")
            )
        assert client.speak_text.call_args.kwargs["text"] == "typed"

    def test_ci_only_keeps_reading_piped_stdin(self, monkeypatch):
        monkeypatch.setattr(speak, "agent_mode", lambda: False)
        assert _pre(["speak"], monkeypatch, text=None) is None


# ── Audio into a pipe ───────────────────────────────────────────────────


class TestNoOutput:
    def test_refused_with_a_rerun_that_saves_the_file(self, monkeypatch):
        result = _pre(["speak", "hi", "-m", "aura-2-asteria-en"], monkeypatch)
        assert isinstance(result, SpeakCodedError)
        assert result.code == "invalid_argument"
        assert (
            result.error == "dg speak won't write audio into this pipe in agent mode."
        )
        tokens = shlex.split(result.next_step)
        assert tokens[:6] == [
            "dg",
            "speak",
            "hi",
            "-m",
            "aura-2-asteria-en",
            "--out-file",
        ]
        out_file = tokens[6]
        assert OUT_FILE.match(out_file) and out_file.endswith(".mp3")
        assert result.hint == (
            f"next_step saves the audio to {out_file}. Give the user that path."
        )
        assert not os.path.exists(out_file)
        assert_e10(result.next_step)

    def test_flux_default_proposes_a_wav(self, monkeypatch):
        result = _pre(["speak", "hi"], monkeypatch, model=None)
        assert shlex.split(result.next_step)[-1].endswith(".wav")

    def test_root_options_are_kept_verbatim(self, monkeypatch):
        result = _pre(["--profile", "work", "speak", "hi"], monkeypatch)
        assert shlex.split(result.next_step)[:4] == ["dg", "--profile", "work", "speak"]

    def test_text_from_stdin_has_no_next_step(self, monkeypatch):
        result = _pre(["speak", "-"], monkeypatch, text="-")
        assert result.next_step is None
        out_file = result.hint.split("--out-file ")[1].split(" ")[0]
        assert OUT_FILE.match(out_file)
        assert result.hint == (
            f"Run the same command again with --out-file {out_file} added. If "
            "the text came from stdin, send it again the same way."
        )

    def test_secret_on_argv_nulls_next_step(self, monkeypatch):
        result = _pre(["--api-key", "sk_sentinel_123", "speak", "hi"], monkeypatch)
        assert result.next_step is None
        # The text was an argument: never ask for the key to be put back.
        out_file = result.hint.split("--out-file ")[1].split(" ")[0]
        assert OUT_FILE.match(out_file)
        assert result.hint == (
            f"Run the same command again with --out-file {out_file} added. If "
            "the text came from stdin, send it again the same way."
        )
        assert "sk_sentinel_123" not in result.model_dump_json()

    @pytest.mark.parametrize(
        "argv",
        [
            ["--api-key=sk_x", "speak", "hi"],
            ["--base-url", "https://user:pw@example.com", "speak", "hi"],
            ["--base-url", "https://example.com/?token=x", "speak", "hi"],
        ],
    )
    def test_every_secret_form_nulls_next_step(self, monkeypatch, argv):
        result = _pre(argv, monkeypatch)
        assert result.next_step is None
        assert result.hint.startswith("Run the same command again")

    @pytest.mark.parametrize(
        "text",
        [
            "Q&A starts now",
            "a < b; echo $HOME | x",
            "It's Q & A time",
            "I'll sudo it",
            "Hi; rm it",
            "I will sudo it, #1 now.",
            "How are you?",
        ],
    )
    def test_shell_metacharacters_stay_inside_one_quoted_argument(
        self, monkeypatch, text
    ):
        result = _pre(["speak", text], monkeypatch, text=text)
        if os.name == "nt" and any(ch in text for ch in "&<>|$;'?"):
            # & < > | $ have no quoting that every Windows shell honours,
            # and ; ' ? run code or glob once an agent wraps the line in
            # another shell: no next_step, and the hint says to run it again.
            assert result.next_step is None
            assert result.hint.startswith("Run the same command again")
            return
        if os.name == "nt":
            assert result.next_step.startswith(f'dg speak "{text}" --out-file ')
            return
        assert_e10(result.next_step)
        assert shlex.split(result.next_step)[2] == text

    def test_text_with_spaces_gets_a_quoted_next_step(self, monkeypatch):
        # Windows refuses the apostrophe (see the test above).
        text = "Its time, now" if os.name == "nt" else "It's time"
        result = _pre(["speak", text], monkeypatch, text=text)
        out_file = result.next_step.split(" ")[-1]
        assert OUT_FILE.match(out_file)
        if os.name == "nt":
            expected = 'dg speak "Its time, now"'
        else:
            expected = "dg speak 'It'\"'\"'s time'"
        assert result.next_step == f"{expected} --out-file {out_file}"
        assert result.hint == (
            f"next_step saves the audio to {out_file}. Give the user that path."
        )

    @pytest.mark.parametrize(
        ("os_name", "tokens", "expected"),
        [
            # POSIX quotes with shlex.
            ("posix", ["dg", "speak", "hello"], "dg speak hello"),
            ("posix", ["dg", "speak", "hi there"], "dg speak 'hi there'"),
            ("posix", ["dg", "speak", "Q&A"], "dg speak 'Q&A'"),
            ("posix", ["dg", "speak", "-o.wav"], "dg speak -o.wav"),
            ("posix", ["dg", "speak", "It's"], "dg speak 'It'\"'\"'s'"),
            ("posix", ["dg", "speak", "x;y"], "dg speak 'x;y'"),
            # Windows: double quotes when a token needs them, None when no
            # form reaches dg unchanged from cmd, PowerShell and Git Bash.
            ("nt", ["dg", "speak", "hello"], "dg speak hello"),
            ("nt", ["dg", "speak", "hi there"], 'dg speak "hi there"'),
            ("nt", ["dg", "speak", "a,b"], 'dg speak "a,b"'),
            ("nt", ["dg", "speak", "-o.wav"], 'dg speak "-o.wav"'),
            ("nt", ["dg", "speak", "Q&A"], None),
            ("nt", ["dg", "speak", "Hi!"], None),
            ("nt", ["dg", "speak", "say “hi”"], None),
            ("nt", ["dg", "speak", "/x"], None),
            # Code runs once an agent wraps the line in another shell.
            ("nt", ["dg", "speak", "It's"], None),
            ("nt", ["dg", "speak", "x;y"], None),
            ("nt", ["dg", "speak", "(x)"], None),
            ("nt", ["dg", "speak", "@{a=1}"], None),
            ("nt", ["dg", "speak", "How are you?"], None),
            ("posix", ["dg", "speak", "How are you?"], "dg speak 'How are you?'"),
            ("nt", ["dg", "speak", "-x:"], 'dg speak "-x:"'),
            ("nt", ["sudo", "speak", "hi"], None),
        ],
    )
    def test_next_step_quotes_or_refuses_by_platform(
        self, monkeypatch, os_name, tokens, expected
    ):
        # Only the command module's view of os.name changes, not the
        # process's, so pathlib keeps working.
        monkeypatch.setattr(speak, "os", types.SimpleNamespace(name=os_name))
        assert speak._next_step(tokens) == expected

    @pytest.mark.parametrize(
        ("prog", "expected"),
        [
            ("C:/venv/Scripts/dg.exe", "C:/venv/Scripts/dg.exe"),
            ("dg", "dg"),
            (None, "dg"),
        ],
    )
    def test_prog_on_windows_is_transport_prog(self, monkeypatch, prog, expected):
        # argv[0] has no .exe on Windows, so it never matches which("dg"):
        # off PATH the launcher's forward-slash path is the runnable one.
        from deepctl_core import transport

        monkeypatch.setattr(speak, "os", types.SimpleNamespace(name="nt", path=os.path))
        monkeypatch.setattr(transport, "prog", lambda: prog)
        assert speak._prog() == expected

    def test_prog_on_posix_does_not_ask_transport(self, monkeypatch):
        from deepctl_core import transport

        monkeypatch.setattr(
            speak, "os", types.SimpleNamespace(name="posix", path=os.path)
        )
        monkeypatch.setattr(transport, "prog", lambda: "/opt/venv/bin/dg")
        monkeypatch.setattr(speak.sys, "argv", ["/opt/venv/bin/dg"])
        monkeypatch.setattr(speak.shutil, "which", lambda name: None)
        assert speak._prog() == "dg"

    def test_windows_next_step_off_path_runs_the_launcher(self, monkeypatch):
        from deepctl_core import transport

        monkeypatch.setattr(transport, "prog", lambda: "C:/venv/Scripts/dg.exe")
        monkeypatch.setattr(speak, "os", types.SimpleNamespace(name="nt", path=os.path))
        assert speak._next_step([speak._prog(), "speak", "hi there"]) == (
            'C:/venv/Scripts/dg.exe speak "hi there"'
        )

    def test_a_token_starting_with_equals_nulls_next_step(self, monkeypatch):
        result = _pre(["speak", "=cmd"], monkeypatch, text="=cmd")
        assert result.next_step is None
        assert result.hint.startswith("Run the same command again")

    @pytest.mark.parametrize(
        "kw", [{"play": True}, {"list_voices": True}, {"out_file": "a.wav"}]
    )
    def test_exempt(self, monkeypatch, kw):
        assert _pre(["speak", "hi"], monkeypatch, **kw) is None

    def test_regular_file_tty_or_dev_null_is_allowed(self, monkeypatch):
        monkeypatch.setattr(sinks, "stdout_takes_audio", lambda: True)
        assert _pre(["speak", "hi"], monkeypatch) is None

    def test_people_and_ci_may_pipe_audio(self, monkeypatch):
        monkeypatch.setattr(speak, "agent_mode", lambda: False)
        assert _pre(["speak", "hi"], monkeypatch) is None


class TestStdoutTakesAudio:
    def test_classification(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sinks, "stdout_takes_audio", _REAL_TAKES_AUDIO)
        f = tmp_path / "out.wav"
        f.write_bytes(b"")
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(f))
        assert _REAL_TAKES_AUDIO() is True
        monkeypatch.setattr(sinks, "stdout_stat", lambda: os.stat(os.devnull))
        assert _REAL_TAKES_AUDIO() is True
        # A pipe with a nonzero index, built by hand: a real os.pipe() has
        # index 0 on Windows, which takes the no-index branch instead.
        fifo = os.stat_result((stat.S_IFIFO | 0o600, 4242, 7, 1, 0, 0, 0, 0, 0, 0))
        monkeypatch.setattr(sinks, "stdout_stat", lambda: fifo)
        monkeypatch.setattr(sinks.os, "isatty", lambda fd: False)
        assert _REAL_TAKES_AUDIO() is False
        monkeypatch.setattr(sinks.os, "isatty", lambda fd: True)
        assert _REAL_TAKES_AUDIO() is True
        monkeypatch.setattr(sinks, "stdout_stat", lambda: None)
        assert _REAL_TAKES_AUDIO() is False


# ── Every speak coded error's next_step ─────────────────────────────────


class TestNextStepRows:
    @pytest.mark.parametrize(
        ("argv", "kw", "expected"),
        [
            (
                ["speak", "hi", "-o", "a", "--out-file", "b"],
                {"output": "a", "out_file": "b"},
                "dg speak --help",
            ),
            (["speak"], {"text": None}, "dg speak --help"),
            (["speak", "hi"], {}, "rerun"),
            (["speak", "-"], {"text": "-"}, None),
            (["speak", "hi", "-o", "json"], {"output": "json"}, "rerun"),
            (["speak", "hi", "-o", "text"], {"output": "text"}, "rerun"),
            (
                ["speak", "hi", "--out-file", "nope/a.wav"],
                {"out_file": "nope/a.wav"},
                None,
            ),
        ],
    )
    def test_next_step_is_runnable_or_null(self, monkeypatch, argv, kw, expected):
        result = _pre(argv, monkeypatch, **kw)
        assert isinstance(result, SpeakCodedError)
        assert_e10(result.next_step)
        if expected is None:
            assert result.next_step is None
        elif expected == "rerun":
            assert "--out-file" in shlex.split(result.next_step)
        else:
            assert result.next_step == expected
        # hint, code and schema_version are never null; details omitted
        # when empty.
        dump = result.model_dump()
        assert dump["hint"] and dump["code"] and dump["schema_version"] == 1
        assert list(dump)[:6] == [
            "error",
            "status",
            "code",
            "hint",
            "next_step",
            "schema_version",
        ]
