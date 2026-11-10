"""dg init outside a terminal: setup needs --yes (or --install, without an
AI agent), the start step needs --start, and what --yes runs is named
before anything is cloned."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_init import clones, lifecycle
from deepctl_cmd_init import command as init_command
from deepctl_cmd_init.command import InitCommand
from deepctl_cmd_init.models import InitCodedError, InitResult, TemplateDetail
from deepctl_core import AuthManager, Config, DeepgramClient

# The shared harness, loaded by path under its own name: a relative import
# depends on which package pytest's importlib mode names "tests.unit".
_HARNESS = "deepctl_cmd_init_tests_harness"
if _HARNESS not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _HARNESS, Path(__file__).with_name("_init_harness.py")
    )
    assert _spec is not None and _spec.loader is not None
    sys.modules[_HARNESS] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_HARNESS])
Run = sys.modules[_HARNESS].Run
template_detail = sys.modules[_HARNESS].template_detail

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Run:
    return Run(tmp_path, monkeypatch)


@pytest.fixture
def make_detail() -> Any:
    """``make_detail(install=..., start=..., url=..., name=...)``."""
    return template_detail


def _expected_step(run: Any, *flags: str) -> str | None:
    """The rerun next_step names: a runnable command, or None on Windows when
    any token (the temp path always) needs shell quoting."""
    if os.name == "nt":
        return None
    return " ".join(["dg init node-transcription --dir", str(run.dest), *flags])


class TestListedSteps:
    """Setup commands the template service lists: confirmed before cloning."""

    def test_need_yes_and_nothing_is_written(self, run: Any, make_detail: Any) -> None:
        run.detail = make_detail(install="touch pwned", start="npm start")
        result = run()
        assert isinstance(result, InitCodedError)
        assert result.status == "error"
        assert result.code == "confirmation_required"
        assert "--yes runs touch pwned" in result.error
        assert result.next_step == _expected_step(run, "--no-install", "--no-start")
        assert result.details == {"flag": "--yes"}
        assert result.hint.startswith("Nothing was cloned.")
        assert run.clones == 0
        assert not run.dest.exists()
        assert run.setup_runs == run.start_runs == []

    def test_agent_hint_says_to_ask_the_user(self, run: Any, make_detail: Any) -> None:
        run.detail = make_detail(install="npm install")
        run.agent = True
        result = run(install=True)
        assert result.code == "confirmation_required"
        if os.name == "nt":
            # The temp path needs quoting, so there is no next_step to name.
            assert result.next_step is None
            assert result.hint == (
                "Nothing was cloned. Show the user the setup commands and ask "
                "them to run dg init in their terminal."
            )
        else:
            assert "only if the user approves" in result.hint
        assert run.clones == 0

    def test_agent_hint_without_a_runnable_next_step(
        self, run: Any, make_detail: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("deepctl_core.transport.prog", lambda: None)
        run.detail = make_detail(install="npm install")
        run.agent = True
        result = run()
        assert result.next_step is None
        assert "ask them to run dg init in their terminal" in result.hint

    def test_install_alone_is_not_an_agents_approval(
        self, run: Any, make_detail: Any
    ) -> None:
        run.detail = make_detail(install="npm install")
        run.agent = True
        assert run(install=True).code == "confirmation_required"
        assert run.setup_runs == []

    def test_install_approves_setup_without_an_agent(
        self, run: Any, make_detail: Any
    ) -> None:
        # CI or no terminal, no AI agent: an explicit --install is enough.
        run.detail = make_detail(install="npm install", start="npm start")
        result = run(install=True)
        assert run.setup_runs == ["npm install"]
        assert run.start_runs == []
        assert result.code == "confirmation_required"
        assert result.details == {"flag": "--start"}

    def test_refusal_names_what_yes_runs(self, run: Any, make_detail: Any) -> None:
        run.detail = make_detail(install="touch listed")
        result = run()
        assert result.error == (
            'Template "node-transcription" has setup commands. --yes runs touch '
            "listed, or make check-prereqs and make init if the repository has "
            "a Makefile."
        )

    def test_yes_runs_the_makefile_when_the_repo_has_one(
        self, run: Any, make_detail: Any
    ) -> None:
        # The rule the refusal stated: a Makefile's targets replace the
        # listed commands.
        run.detail = make_detail(install="touch listed")
        run.makefile = True
        refused = run()
        assert "if the repository has a Makefile" in refused.error
        assert run.clones == 0
        result = run(yes=True)
        assert result.status == "success"
        assert run.setup_runs == ["make"]

    @needs_git
    def test_next_step_clones_without_running_them(
        self, run: Any, make_detail: Any
    ) -> None:
        run.detail = make_detail(install="touch pwned", start="npm start")
        result = run(no_install=True, no_start=True)
        assert isinstance(result, InitResult)
        assert result.status == "success"
        assert run.clones == 1
        assert run.setup_runs == run.start_runs == []
        assert run.record() is not None
        assert run.record().phases == ["clone"]  # type: ignore[index]

    @needs_git
    def test_yes_runs_them_but_never_the_start_step(
        self, run: Any, make_detail: Any
    ) -> None:
        run.detail = make_detail(install="npm install", start="npm start")
        result = run(yes=True)
        assert run.setup_runs == ["npm install"]
        assert run.start_runs == []
        assert result.code == "confirmation_required"
        assert result.next_step == _expected_step(run, "--no-start")
        assert result.details == {"flag": "--start"}
        assert run.record().phases == ["clone", "setup"]  # type: ignore[index]

    def test_start_runs_only_when_asked_for(self, run: Any, make_detail: Any) -> None:
        run.detail = make_detail(install="npm install", start="npm start")
        result = run(yes=True, start=True)
        assert result.status == "success"
        assert result.started is True
        assert run.start_runs == ["npm start"]

    def test_a_person_at_a_terminal_keeps_the_old_prompts(
        self, run: Any, make_detail: Any
    ) -> None:
        run.detail = make_detail(install="npm install")
        run.person = True
        with patch.object(InitCommand, "confirm", return_value=True):
            result = run(install=True, no_start=True)
        assert result.status == "success"
        assert run.setup_runs == ["npm install"]


class TestMakefileSteps:
    """A Makefile is only known after the clone: the confirmation comes then,
    and next_step reruns into the clone."""

    @needs_git
    def test_need_yes_after_the_clone(self, run: Any) -> None:
        run.makefile = True
        result = run()
        assert result.code == "confirmation_required"
        assert "make check-prereqs; make init" in result.error
        assert result.hint.startswith("The template is cloned in ")
        assert run.clones == 1
        assert run.setup_runs == []
        assert run.record().phases == ["clone"]  # type: ignore[index]

    @needs_git
    def test_next_step_resumes_without_cloning_again(self, run: Any) -> None:
        run.makefile = True
        first = run()
        if os.name == "nt":
            assert first.next_step is None  # the temp path needs quoting
        else:
            assert first.next_step.endswith("--no-install --no-start")
        result = run(no_install=True, no_start=True)
        assert result.status == "success"
        assert result.installed is False
        assert run.clones == 1
        assert run.setup_runs == []

    @needs_git
    def test_rerun_with_yes_runs_setup_in_the_clone(self, run: Any) -> None:
        run.makefile = True
        run()
        result = run(yes=True)
        assert result.status == "success"
        assert result.installed is True
        assert run.clones == 1
        assert run.setup_runs == ["make"]
        assert run.record().phases == ["clone", "setup"]  # type: ignore[index]

    @needs_git
    def test_finished_setup_is_not_run_again(self, run: Any) -> None:
        run.makefile = True
        run(yes=True)
        run(yes=True)
        assert run.setup_runs == ["make"]

    def test_no_steps_no_confirmation(self, run: Any) -> None:
        result = run()
        assert result.status == "success"
        assert run.clones == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestWithGit:
    """A real clone (from a local repository standing in for GitHub) and the
    template's own steps run by the shell."""

    @pytest.fixture
    def origin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        work = tmp_path / "origin" / "node-transcription"
        work.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
        for args in (
            ["init", "-q", "-b", "main"],
            [
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@e",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "init",
            ],
        ):
            subprocess.run(["git", *args], cwd=work, env=env, check=True)
        gitconfig = tmp_path / "gitconfig"
        gitconfig.write_text(
            f'[url "{work.parent.as_uri()}/"]\n'
            "\tinsteadOf = https://github.com/deepgram-starters/\n"
            '[protocol "file"]\n\tallow = always\n'
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        (tmp_path / "home").mkdir()
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr("deepctl_core.transport.prog", lambda: "dg")
        monkeypatch.setattr(
            "deepctl_cmd_init.command._person_at_terminal", lambda: False
        )
        monkeypatch.setattr("deepctl_cmd_init.command._hard_signal", lambda: True)
        monkeypatch.setattr(lifecycle, "check_prereqs", lambda: [])
        return work

    def _run(self, detail: TemplateDetail, dest: Path, **flags: Any) -> Any:
        kwargs = {
            "template": detail.name,
            "dir": str(dest),
            "search": None,
            "list": False,
            "install": False,
            "no_install": False,
            "start": False,
            "no_start": False,
            "yes": False,
            **flags,
        }
        auth = Mock(spec=AuthManager)
        auth.get_api_key.return_value = None
        with patch(
            "deepctl_cmd_init.command.templates_api.get_template", return_value=detail
        ):
            return InitCommand().handle(
                config=Mock(spec=Config),
                auth_manager=auth,
                client=Mock(spec=DeepgramClient),
                **kwargs,
            )

    def test_steps_run_only_with_yes(
        self, origin: Path, tmp_path: Path, make_detail: Any
    ) -> None:
        pwned = tmp_path / "pwned"
        started = tmp_path / "pwned-start"
        detail = make_detail(install=f"touch {pwned}", start=f"touch {started}")
        dest = tmp_path / "app"
        refused = self._run(detail, dest)
        assert refused.code == "confirmation_required"
        assert not pwned.exists() and not dest.exists()

        done = self._run(detail, dest, no_install=True, no_start=True)
        assert done.status == "success"
        assert not pwned.exists() and not started.exists()
        record = clones.load(os.path.realpath(dest))
        assert record is not None and len(record.commit) == 40
        status = subprocess.run(
            ["git", "-C", str(dest), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert status.stdout == ""

        again = self._run(detail, dest, yes=True)
        assert pwned.exists()
        assert not started.exists()
        assert again.code == "confirmation_required"
        assert again.details == {"flag": "--start"}


def test_python_m_has_no_runnable_next_step(
    run: Any, make_detail: Any, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["python", "-m", "deepctl"])
    monkeypatch.setattr("deepctl_core.transport.prog", lambda: None)
    run.detail = make_detail(install="npm install")
    result = run()
    assert result.next_step is None
    assert result.code == "confirmation_required"


def test_agent_without_a_template_gets_the_coded_pick_error(
    run: Any, monkeypatch, capsys
) -> None:
    """No picker in an AI agent session: the catalogue's E-CONFIRM-INIT-PICK,
    with dg init --list as next_step."""
    monkeypatch.setattr("deepctl_cmd_init.command._agentic", True)
    result = run(template=None)
    assert isinstance(result, InitCodedError)
    assert result.code == "invalid_argument"
    assert result.error == (
        "dg init needs a template name when it can't show the picker."
    )
    assert result.hint == (
        "List templates with dg init --list, then run dg init <TEMPLATE>."
    )
    assert result.next_step == "dg init --list"
    assert run.clones == 0


@needs_git
def test_unsaved_progress_says_a_rerun_wont_resume(
    run: Any, monkeypatch, capsys
) -> None:
    def refuse(record: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(clones, "save", refuse)
    assert run(no_install=True).status == "success"
    out = " ".join(capsys.readouterr().out.split())
    assert "Couldn't save setup progress (disk full), so running dg init " in out
    assert "again won't resume in" in out


@pytest.mark.parametrize(
    ("os_name", "dest", "expected"),
    [
        # POSIX quotes with shlex.
        ("posix", "/work/app", "dg init tpl --dir /work/app --no-start"),
        ("posix", "/work/my app", "dg init tpl --dir '/work/my app' --no-start"),
        # Windows has no shell quoting to rely on: None when a token needs
        # quoting, the plain command when none does.
        ("nt", "/work/app", "dg init tpl --dir /work/app --no-start"),
        ("nt", "/work/my app", None),
    ],
)
def test_rerun_quotes_or_refuses_by_platform(
    monkeypatch: pytest.MonkeyPatch, os_name: str, dest: str, expected: str | None
) -> None:
    monkeypatch.setattr("deepctl_core.transport.prog", lambda: "dg")
    # Only the command module's view of os.name changes, not the process's,
    # so pathlib keeps working; PurePosixPath prints the same on every OS.
    monkeypatch.setattr(init_command, "os", SimpleNamespace(name=os_name))
    assert init_command._rerun("tpl", PurePosixPath(dest), "--no-start") == expected


@pytest.mark.parametrize("os_name", ["posix", "nt"])
def test_rerun_refuses_an_equals_token_and_an_unknown_prog(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    monkeypatch.setattr(init_command, "os", SimpleNamespace(name=os_name))
    dest = PurePosixPath("/work/app")
    monkeypatch.setattr("deepctl_core.transport.prog", lambda: "dg")
    assert init_command._rerun("=tpl", dest, "--no-start") is None
    monkeypatch.setattr("deepctl_core.transport.prog", lambda: None)
    assert init_command._rerun("tpl", dest, "--no-start") is None
