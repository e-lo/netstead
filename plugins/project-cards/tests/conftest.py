"""Fixtures: a Workbench session with the project cards plugin on the Leavenworth fixture, and a node runner."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import OpenNetwork
from netstead_project_cards import plugin

SRC = str(leavenworth.parquet_dir())
NET = "leavenworth"


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, str]:
    """User config under ``tmp_path``; ``io.allowed_roots`` is ``tmp_path`` plus the read-only fixture."""
    roots = [str(tmp_path.resolve()), str(Path(SRC).resolve().parent)]
    return {"NETSTEAD_CONFIG_DIR": str(tmp_path / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps(roots)}


@pytest.fixture
def make_session(tmp_path: Path, isolated_env: dict[str, str]) -> Callable[..., Session]:
    """``make_session(*extra_plugins, provider=None, overrides=None, open_network=True, enable_apply=True)``.

    The cards plugin is always installed. Its ``enable_apply`` setting is on here (it is off by default) so the
    apply_card tests run; ``enable_apply=False`` is the shipped default.
    """

    def build(
        *extra: Any,
        provider: Any = None,
        overrides: dict[str, Any] | None = None,
        open_network: bool = True,
        enable_apply: bool = True,
    ) -> Session:
        session = Session(
            project_dir=tmp_path,
            environ=isolated_env,
            parser=StubParser(),
            plugins=[plugin(), *extra],
            assistant_provider=provider,
            overrides={"plugins.project_cards.enable_apply": enable_apply, **(overrides or {})},
            keyring=None,  # never touch the real OS keychain
        )
        if open_network:
            session.dispatch(OpenNetwork(source=SRC))
        return session

    return build


@pytest.fixture
def session(make_session: Callable[..., Session]) -> Session:
    return make_session()


@pytest.fixture
def host(session: Session) -> Any:
    """The project cards plugin's Host in ``session`` (what its handlers receive)."""
    return session._hosts["project_cards"]


@pytest.fixture
def no_state_reads(monkeypatch):
    """``install(session)``: plugin ``state()`` is never called; only the handlers check the drafts and preview."""

    def install(session):
        monkeypatch.setattr(session, "_plugin_state", dict)

    return install


@pytest.fixture
def race_before(host: Any, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """``race_before("plan_update")``: that ``host`` method's first call lets another edit land (lanes = 7 on link 3).

    It then plans against a newer network than the change read, which is what a concurrent edit looks like.
    """

    def install(name: str) -> None:
        real, plan_update, raced = getattr(host, name), host.plan_update, []

        def racing(*args: Any, **kwargs: Any) -> Any:
            if not raced:
                raced.append(True)
                host.mutate(NET, plan_update(NET, "link", [3], {"lanes": 7}).edits, note="race")
            return real(*args, **kwargs)

        monkeypatch.setattr(host, name, racing)

    return install


# Unused in PR A: PR B's JS tests use it.
@pytest.fixture
def run_js(tmp_path: Path) -> Callable[[dict[str, Path], str], Any]:
    """``run_js({"a.js": path, ...}, harness)``: copy the files, run ``harness`` as an ES module, return its JSON.

    The harness prints one JSON value with ``console.log(JSON.stringify(...))``. Launched with an absolute path and
    ``close_fds=False`` so :mod:`subprocess` uses ``posix_spawn`` (forking a busy xdist worker can crash on macOS).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    counter = iter(range(1_000_000))

    def run(files: dict[str, Path], harness: str) -> Any:
        root = tmp_path / f"js{next(counter)}"
        root.mkdir()
        for name, source in files.items():
            shutil.copy(source, root / name)
        (root / "harness.mjs").write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            [node, str(root / "harness.mjs")], stdin=subprocess.DEVNULL, capture_output=True, text=True, close_fds=False
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run
