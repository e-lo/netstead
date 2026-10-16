"""Shared pytest fixtures and session-wide guards for the netstead suite.

The committed fixture data under ``netstead/fixtures`` (the Leavenworth
network in csv / parquet / duckdb / zip form) is read-only reference
data.  Tests must read it and write any derived output to ``tmp_path``.

A test that instead writes a network *into* the fixture directory
mutates version-controlled files and leaves the git tree dirty after a
plain ``pytest`` run.  That has caused accidental fixture commits and
forced ``git checkout`` dances before every rebase.  The autouse guard
below snapshots the fixture files once per session and fails loudly at
teardown if any changed, naming the offending paths so the write can be
redirected to ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

import netstead.fixtures
import pytest

_FIXTURES_ROOT = Path(netstead.fixtures.__file__).parent


def _data_files() -> list[Path]:
    """Every committed fixture *data* file (not Python, not caches)."""
    return sorted(
        p for p in _FIXTURES_ROOT.rglob("*") if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
    )


def _snapshot() -> dict[Path, str]:
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in _data_files()}


@pytest.fixture(scope="session", autouse=True)
def _fixture_data_is_read_only() -> None:
    """Fail the session if any test mutates committed fixture data.

    A failure means a test wrote into ``netstead/fixtures`` instead of
    ``tmp_path``; redirect that write to the ``tmp_path`` the test is
    given (or a copy of the fixture made there).
    """
    before = _snapshot()
    yield
    after = _snapshot()

    changed = sorted(
        str(p.relative_to(_FIXTURES_ROOT)) for p in set(before) | set(after) if before.get(p) != after.get(p)
    )
    assert not changed, (
        "Tests mutated committed fixture data under netstead/fixtures; "
        "write derived output to tmp_path, not the fixture dir. "
        f"Changed files: {changed}"
    )


@pytest.fixture(scope="session")
def rdu_source() -> str:
    """Path to the committed RDU I-40 parquet fixture network (read-only)."""
    return str(resources.files("netstead.fixtures.rdu_i40").joinpath("parquet"))


#: Committed test-only fixture files (tests/fixtures), e.g. the local OSM/Overture inputs.
TEST_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, str]:
    """An environ isolated from the real machine.

    The netstead user-config dir lives under ``tmp_path`` (never the real ``~/.config``), and
    ``io.allowed_roots`` is pinned to ``tmp_path`` plus the two read-only fixture trees, so the
    workbench's allowed-roots policy behaves the same wherever the repo is checked out.
    """
    roots = [str(tmp_path.resolve()), str(_FIXTURES_ROOT.resolve()), str(TEST_FIXTURES)]
    return {"NETSTEAD_CONFIG_DIR": str(tmp_path / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps(roots)}


class FakeKeyring:
    """In-memory stand-in for the ``keyring`` module: tests never touch the real OS keychain."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.store.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.store[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        del self.store[(service_name, username)]


@pytest.fixture
def fake_keyring() -> FakeKeyring:
    """A fresh in-memory keyring."""
    return FakeKeyring()


class FakeAPI:
    """Canned JSON per ``(METHOD, path)`` behind an ``httpx.MockTransport``; every request is kept.

    ``add`` queues responses for a route; once one is left it repeats. ``raises`` makes the
    transport raise that exception instead (e.g. ``httpx.ConnectError("refused")``). A ``bytes``
    body is sent as-is (e.g. Ollama's newline-delimited JSON pull progress).
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[tuple[int, Any, dict[str, str], Exception | None]]] = {}
        self.requests: list[Any] = []

    def add(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
        raises: Exception | None = None,
    ) -> FakeAPI:
        self.routes.setdefault((method.upper(), path), []).append((status, body, headers or {}, raises))
        return self

    def transport(self) -> Any:
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            queue = self.routes.get((request.method, request.url.path))
            if not queue:
                return httpx.Response(599, json={"error": f"no fake route for {request.method} {request.url.path}"})
            status, body, headers, raises = queue.pop(0) if len(queue) > 1 else queue[0]
            if raises is not None:
                raise raises
            if isinstance(body, bytes):
                return httpx.Response(status, content=body, headers=headers)
            return httpx.Response(status, json=body, headers=headers)

        return httpx.MockTransport(handler)

    def body(self, index: int = -1) -> dict[str, Any]:
        """The JSON body of a recorded request (default: the last one)."""
        return json.loads(self.requests[index].content)


@pytest.fixture
def fake_api() -> FakeAPI:
    """A fresh fake provider API."""
    return FakeAPI()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if code under test opens a real HTTP connection (LLM tests use ``fake_api``)."""
    httpx = pytest.importorskip("httpx")

    def refuse(self: Any, request: Any) -> Any:
        raise AssertionError(f"real network call in a test: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def _no_system_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test reach the developer's real keychain: ``keyring="auto"`` resolves to "none".

    Tests that need a keyring pass ``fake_keyring`` explicitly.
    """
    monkeypatch.setattr("netstead.llm.secrets.system_keyring", lambda: None)


@pytest.fixture(autouse=True)
def _no_installed_workbench_plugins(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A default ``Session()`` discovers no entry-point plugins, so a run matches CI's (which installs ``hello``).

    Tests that exercise real discovery opt in with ``@pytest.mark.plugin_discovery``.
    """
    if request.node.get_closest_marker("plugin_discovery") is not None:
        return
    try:
        from netstead.workbench.plugins import discovery
    except ImportError:  # the workbench extra isn't installed: nothing can discover plugins
        return
    monkeypatch.setattr(discovery, "entry_points", lambda **kwargs: [])


@pytest.fixture
def run_node():
    """Run ``node`` with captured text output, launched via ``posix_spawn`` rather than fork.

    A pytest-xdist worker is heavily multithreaded (DuckDB, etc.). On macOS, forking such a
    process occasionally crashes the child before it execs (exit -11, no output), which showed
    up as ~1-in-4 flaky full parallel runs. An absolute executable path with
    ``close_fds=False`` lets :mod:`subprocess` use ``posix_spawn``, which never forks the
    worker.
    """

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        node = shutil.which("node")
        assert node, "node not installed"
        return subprocess.run([node, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, close_fds=False)

    return run


#: The workbench's ES modules (resolved without importing the FastAPI app).
WORKBENCH_JS = _FIXTURES_ROOT.resolve().parent / "workbench" / "static" / "js"


#: A statement that names another module: ``import … from "…"`` (one line or several), ``export {…} from "…"`` /
#: ``export * from "…"``, or a bare ``import "…"``. Group 1 or 2 is the specifier.
_MODULE_REF = re.compile(
    r'^[ \t]*(?:import\b|export\s*(?:\*|\{))[^;"]*?\bfrom\s*"([^"]*)"|^[ \t]*import\s*"([^"]*)"', re.M
)
#: Where every import statement starts: each must be one :data:`_MODULE_REF` could read.
_IMPORT_START = re.compile(r"^[ \t]*import\b.*$", re.M)
#: A sibling pure module's specifier (``"./x.js"``), as written in a ``from`` clause or a bare import.
_SIBLING_SPEC = re.compile(r'(\bfrom\s*|\bimport\s*)"\./([\w-]+)\.js"')


def _copy_pure(path: Path, dest: Path, copied: set[str]) -> str:
    """Copy ``path`` into ``dest`` as ``.mjs`` with the sibling modules it imports (recursively); return its name.

    Node then treats them as ES modules without a package.json. A pure module may import (or re-export from) only
    other pure modules beside it, written with double quotes; any other import (a bare or remote one, or one this
    scan can't read) fails loudly rather than leaving a module behind.
    """
    name = f"{path.stem}.mjs"
    if name in copied:
        return name
    copied.add(name)
    source = path.read_text(encoding="utf-8")
    refs = list(_MODULE_REF.finditer(source))
    readable = {m.start() for m in refs}
    for start in _IMPORT_START.finditer(source):
        assert start.start() in readable, (
            f'{path.name}: can\'t read this import (use `import {{ a }} from "./b.js";`): {start.group(0).strip()}'
        )
    for ref in refs:
        spec = ref.group(1) if ref.group(1) is not None else ref.group(2)
        sibling = re.fullmatch(r"\./([\w-]+)\.js", spec)
        assert sibling, f"{path.name}: a pure module may import only sibling pure modules, not: {spec!r}"
        _copy_pure(path.with_name(f"{sibling.group(1)}.js"), dest, copied)
    (dest / name).write_text(_SIBLING_SPEC.sub(r'\1"./\2.mjs"', source), encoding="utf-8")
    return name


@pytest.fixture
def node_module(tmp_path: Path, run_node):
    """Evaluate a JS expression against a pure workbench module under node; return its JSON value.

    ``node_module("linking.js", ["pageOffset"], "pageOffset(250, 100)")`` -> ``200``. ``module`` is a file
    in the workbench's ``static/js``, or an absolute path (a test's own harness). Pure modules may import
    sibling pure modules, which are copied along; ``expr`` may use top-level ``await``.
    """
    counter = iter(range(1_000_000))

    def run(module: str | Path, names: list[str], expr: str) -> Any:
        root = tmp_path / f"node{next(counter)}"
        root.mkdir()
        path = Path(module) if Path(module).is_absolute() else WORKBENCH_JS / module
        entry = _copy_pure(path, root, set())
        script = root / "__probe__.mjs"
        script.write_text(f'import {{ {", ".join(names)} }} from "./{entry}";\nconsole.log(JSON.stringify({expr}));\n')
        proc = run_node([str(script)])
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run
