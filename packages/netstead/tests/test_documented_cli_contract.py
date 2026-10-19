"""Contract test: every CLI flag the docs reference must exist.

**Why this test exists.** The Python-API contract test (``test_documented_api_contract.py``)
catches doc-vs-code drift for ``netstead.X`` / ``corral.X`` symbols. It doesn't catch
**CLI** drift — the v1.0 walk-through on 2026-05-26 found ``netstead validate --report=html
-o PATH`` documented but never implemented (only ``--json`` existed).

This test closes that gap. It walks every ``.md`` doc, finds every
``netstead ...`` / ``corral ...`` invocation in a bash/zsh/console
fenced block (or inline backticks), and asserts every ``--flag``
mentioned actually exists on the resolved command.

**Implementation:**

1. Build the typer apps + convert them to click Groups so we can
   introspect per-command parameter lists.
2. Walk each doc, extract every ``$ netstead …`` / ``$ corral …`` /
   bare ``netstead …`` invocation. Strip ``uv run`` prefix.
3. Parse via ``shlex`` to split into subcommand path + flags.
4. Look up the resolved command's flag set via click's
   ``Command.params``. Assert every documented flag is present.

**Scope decisions:**

- Only checks the **flag names** (``--html``, ``--json``, ``-y``).
  Doesn't validate flag values, types, or behavior — those are the
  CLI's own tests.
- Skips invocations whose subcommand path can't be resolved (e.g.
  documented commands that don't exist) — those are flagged as the
  resolution-error case rather than the flag-missing case.
- Skips placeholder flags (``--<placeholder>``) and obviously-templated
  tokens (``${VAR}``, ``$LV``).
- Whitelist entries for env-var-shaped tokens, prose snippets that
  look like commands but aren't.

When a CLI flag is removed or renamed, the test fails listing every
doc that references the old name. When a doc is written that promises
a flag we never implemented, same.

**Documented JSON output.** A second, slow test executes documented
``--json`` invocations. When a bash fence holds a single ``netstead …``
/ ``corral …`` command with ``--json`` and is followed (blank lines
allowed) by a line ``Expected:`` and a ```json fence, the command runs
from the repo root and its stdout is parsed and compared with the
documented JSON — same keys, same values, recursively. A documented
value of ``"..."`` (or a bare ``...``) is a wildcard for anything, so
timings and other nondeterministic values can be elided. The usual
``<!-- doctest: skip -->`` marker before the bash fence opts out.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest
from typer.main import get_command

# Repo layout: packages/netstead/tests/test_documented_cli_contract.py
REPO_ROOT = Path(__file__).resolve().parents[3]

# Markdown files to scan. Same set as the Python-API contract test —
# we want the two checks to share a "what counts as a doc" definition.
_DOC_GLOBS = [
    "packages/corral/README.md",
    "packages/corral/docs/**/*.md",
    "packages/netstead/README.md",
    "packages/netstead/docs/**/*.md",
]

# Patterns:
#   1. Fenced code block tagged bash/zsh/sh/console — any line that
#      starts with `netstead` or `corral` (optionally prefixed with
#      `uv run `, `$ `, or both).
#   2. Inline backtick: `netstead X --y` (case-sensitive, single backticks).
_FENCE_RE = re.compile(
    r"```(?:bash|sh|shell|console|zsh)\n(.+?)\n```",
    flags=re.DOTALL,
)
_INVOCATION_LINE_RE = re.compile(
    r"^\s*(?:\$\s+)?(?:uv\s+run\s+)?(netstead|corral)\s+(.+?)$",
    flags=re.MULTILINE,
)
_INLINE_RE = re.compile(
    r"`(?:uv\s+run\s+)?(netstead|corral)\s+([^`]+?)`",
)

# Flags that appear as PROSE placeholders, not real invocations
# (e.g. "use --report=html" inside a sentence describing what to add).
# Add entries here only after confirming the doc text is clearly prose
# referring to a flag that exists OR is intentionally documented as
# not-yet-implemented.
_KNOWN_PROSE_FLAGS: set[tuple[str, tuple[str, ...], str]] = set()
# format: (package, command_path, flag)
# e.g. ("netstead", ("validate",), "--report=html")


def _extract_invocations() -> list[tuple[str, str, list[str], Path]]:
    """Walk every doc, return [(package, raw_args, mentioning_file), ...].

    package is "netstead" or "corral"; raw_args is the post-command-
    name argv as a single string (later parsed via shlex).
    """
    out: list[tuple[str, str, list[str], Path]] = []
    for glob in _DOC_GLOBS:
        for path in REPO_ROOT.glob(glob):
            # Skip generated docs + the migration guide (which deliberately
            # quotes old-API forms as the "before" side).
            if path.name.startswith("llms") or path.name == "api-index.json":
                continue
            if path.name == "v0.3-to-v1.0.md":
                continue
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT)

            # 1) Lines inside bash/zsh fenced blocks.
            for fence in _FENCE_RE.finditer(text):
                block = fence.group(1)
                for inv in _INVOCATION_LINE_RE.finditer(block):
                    pkg, args = inv.group(1), inv.group(2).strip()
                    # Strip trailing line-continuation + inline comment.
                    args = re.sub(r"\s+#.*$", "", args)
                    args = args.rstrip("\\").strip()
                    if args:
                        out.append((pkg, args, [], rel))

            # 2) Inline backtick references — `netstead info --json`.
            for inv in _INLINE_RE.finditer(text):
                pkg, args = inv.group(1), inv.group(2).strip()
                if args:
                    out.append((pkg, args, [], rel))
    return out


def _parse(pkg: str, raw_args: str) -> tuple[tuple[str, ...], list[str]] | None:
    """Split a raw arg string into (command_path, flags).

    Returns None when the args can't be safely parsed (shlex failure,
    leading `--`, etc.) — better to skip than to false-positive.
    """
    try:
        tokens = shlex.split(raw_args, comments=True)
    except ValueError:
        return None
    command_path: list[str] = []
    flags: list[str] = []
    seen_flag = False
    for tok in tokens:
        # Shell pipeline / redirection — everything after belongs to a
        # different command (e.g. `netstead bench --json | jq -r ...`).
        # shlex treats these as ordinary tokens, so we stop here.
        if tok in ("|", "||", "&&", ";", ">", ">>", "<", "<<", "2>", "2>>", "&"):
            break
        # Stop accumulating command words once we see the first flag.
        if tok.startswith("--"):
            seen_flag = True
            flag_name = tok.split("=", 1)[0]
            flags.append(flag_name)
        elif tok.startswith("-") and len(tok) >= 2 and tok[1].isalpha():
            seen_flag = True
            flags.append(tok)
        elif not seen_flag:
            # Pure positional before any flag → looks like a subcommand
            # word UNLESS it's an obvious value (contains /, =, $, etc.).
            if any(c in tok for c in "/=$") or tok.startswith("."):
                # First positional that looks like a path — stop collecting
                # subcommand words (the rest are arg values).
                seen_flag = True  # bail out of subcommand collection
            else:
                command_path.append(tok)
        # else: positional after a flag — ignore (it's a flag value).
    return tuple(command_path), flags


#: Flags click attaches to EVERY command via Context (not via params),
#: so we add them to every command's flag set explicitly.
_UNIVERSAL_FLAGS = {"--help", "-h"}


def _build_cli_map() -> dict[str, dict[tuple[str, ...], set[str]]]:
    """Build {package: {command_path: {flag, ...}}} from typer's click commands.

    Walks both typer apps recursively. Each leaf command's params are
    introspected for their option strings. Click stores ALL aliases in
    ``Option.opts``, so ``--json`` and ``-j`` both land in the set.
    Universal flags (``--help`` / ``-h``) are unioned in — click adds
    them via Context rather than Command.params.
    """
    from corral.cli.app import build_app as build_corral_app
    from netstead.cli.app import _build_netstead_app

    def walk(cmd, prefix: tuple[str, ...]) -> Iterable[tuple[tuple[str, ...], set[str]]]:
        # Click's Command exposes params (Options + Arguments). Pull
        # everything that has a `--`-style opt.
        flags: set[str] = set(_UNIVERSAL_FLAGS)
        for param in getattr(cmd, "params", []):
            for opt in getattr(param, "opts", []) or []:
                if opt.startswith("-"):
                    flags.add(opt)
            for opt in getattr(param, "secondary_opts", []) or []:
                if opt.startswith("-"):
                    flags.add(opt)
        yield prefix, flags

        # Group → recurse into children.
        commands = getattr(cmd, "commands", None) or {}
        for name, child in commands.items():
            yield from walk(child, (*prefix, name))

    out: dict[str, dict[tuple[str, ...], set[str]]] = {}
    for pkg, app_builder in (("corral", build_corral_app), ("netstead", _build_netstead_app)):
        typer_app = app_builder()
        click_app = get_command(typer_app)
        out[pkg] = dict(walk(click_app, ()))
    return out


def _expand_command_path(
    available: dict[tuple[str, ...], set[str]],
    candidate: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Return the longest known prefix of ``candidate`` that's a real command path.

    Allows users to add a positional arg after the command (``netstead
    info <path> --json``) — we resolve to ``("info",)`` and check its
    flags. Returns None if no prefix resolves.
    """
    for n in range(len(candidate), -1, -1):
        prefix = candidate[:n]
        if prefix in available:
            return prefix
    return None


# Build once at module load — cheap, the typer app construction is the
# same work pytest fixtures do for the CLI tests.
_CLI_MAP = _build_cli_map()


def _invocations_for_parametrize() -> list[tuple[str, tuple[str, ...], str, str, Path]]:
    """Return one entry per (package, resolved_cmd_path, flag, original_args, doc).

    Pytest will then run one test per (cmd, flag) doc reference.
    """
    seen: dict[tuple[str, tuple[str, ...], str], tuple[str, Path]] = {}
    for pkg, raw_args, _, doc in _extract_invocations():
        parsed = _parse(pkg, raw_args)
        if parsed is None:
            continue
        candidate_path, flags = parsed
        available = _CLI_MAP.get(pkg, {})
        resolved = _expand_command_path(available, candidate_path)
        if resolved is None:
            # The subcommand doesn't exist — skip; a separate test
            # could check command-existence if useful.
            continue
        for flag in flags:
            key = (pkg, resolved, flag)
            seen.setdefault(key, (raw_args, doc))
    return [(pkg, path, flag, original, doc) for (pkg, path, flag), (original, doc) in sorted(seen.items())]


_INVOCATIONS = _invocations_for_parametrize()


@pytest.mark.parametrize(
    "package,command_path,flag,original_args,doc",
    _INVOCATIONS,
    ids=[f"{pkg} {' '.join(path)} {flag}".strip() for pkg, path, flag, _, _ in _INVOCATIONS],
)
def test_documented_cli_flag_exists(
    package: str,
    command_path: tuple[str, ...],
    flag: str,
    original_args: str,
    doc: Path,
) -> None:
    """Every ``--flag`` mentioned in a documented CLI invocation must exist.

    Regression closer for the walk-through finding that
    ``netstead validate --report=html`` was documented in the README
    cookbook but the actual command only accepted ``--json``. CI now
    fails immediately when any doc reference drifts away from real
    CLI surface.
    """
    available_flags = _CLI_MAP[package].get(command_path, set())
    cmd_display = f"{package} {' '.join(command_path)}".strip()

    if (package, command_path, flag) in _KNOWN_PROSE_FLAGS:
        pytest.xfail(f"known prose-only reference to {flag} in {cmd_display}")

    assert flag in available_flags, (
        f"Documented CLI flag does not exist: '{cmd_display} {flag}'\n"
        f"  documented in: {doc}\n"
        f"  full invocation: {package} {original_args}\n"
        f"  available flags on {cmd_display!r}: "
        f"{sorted(available_flags) if available_flags else '(none)'}\n\n"
        f"Fix options:\n"
        f"  - Add {flag} to the command's typer signature\n"
        f"  - Update the doc to use the correct flag name\n"
        f"  - Whitelist via _KNOWN_PROSE_FLAGS in {Path(__file__).name}\n"
        f"    if the reference is intentional prose (not an executable example)."
    )


# ---------------------------------------------------------------------------
# Documented `--json` output: execute + compare
# ---------------------------------------------------------------------------

# A bash fence (column 0 or indented) holding ONE command line, then
# `Expected:` and a ```json fence at the same indentation.
_JSON_EXAMPLE_RE = re.compile(
    r"^(?P<indent>[ \t]*)```(?:bash|sh|shell|console|zsh)[ \t]*\n"
    r"(?P<cmd>[^\n]*)\n"
    r"(?P=indent)```[ \t]*\n(?:[ \t]*\n)*"
    r"(?P=indent)Expected:[ \t]*\n(?:[ \t]*\n)*"
    r"(?P=indent)```json[ \t]*\n(?P<json>.*?)\n(?P=indent)```[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)
_SINGLE_COMMAND_RE = re.compile(r"^\s*(?:\$\s+)?(?:uv\s+run\s+)?(netstead|corral)\s+(.+?)\s*$")
_SKIP_MARKER = re.compile(r"^\s*<!--\s*doctest:\s*skip\b.*?-->\s*$")
# A bare `...` used as a JSON value — not valid JSON, so it is rewritten
# to the string wildcard "..." before parsing.
_BARE_ELLIPSIS_RE = re.compile(r'(?<![\w"])\.\.\.(?![\w"])')


def _fence_is_skipped(text: str, fence_start: int) -> bool:
    """Same rule as the python contract test: a `<!-- doctest: skip -->`
    among the three non-blank lines before the fence (not past another
    fence) opts the block out."""
    seen = 0
    for line in reversed(text[:fence_start].splitlines()):
        if not line.strip():
            continue
        if line.strip().startswith("```"):
            return False
        if _SKIP_MARKER.match(line):
            return True
        seen += 1
        if seen >= 3:
            return False
    return False


def _dedent_by(body: str, indent: str) -> str:
    if not indent:
        return body
    return "\n".join(ln[len(indent) :] if ln.startswith(indent) else ln for ln in body.split("\n"))


def _collect_json_examples() -> list[tuple[Path, int, str, list[str], str]]:
    """Return [(doc, fence_line_no, package, argv, documented_json), ...]."""
    out: list[tuple[Path, int, str, list[str], str]] = []
    for glob in _DOC_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if path.name.startswith("llms") or path.name == "api-index.json":
                continue
            if path.name == "v0.3-to-v1.0.md":
                continue
            text = path.read_text(encoding="utf-8")
            for m in _JSON_EXAMPLE_RE.finditer(text):
                if _fence_is_skipped(text, m.start()):
                    continue
                cmd = _SINGLE_COMMAND_RE.match(m.group("cmd"))
                if cmd is None:
                    continue
                pkg, raw_args = cmd.group(1), cmd.group(2)
                try:
                    argv = shlex.split(raw_args, comments=True)
                except ValueError:
                    continue
                # Only plain invocations: no pipes / redirects, and --json.
                if "--json" not in argv or any(tok in ("|", "||", "&&", ";", ">", ">>", "<") for tok in argv):
                    continue
                line_no = text[: m.start()].count("\n") + 1
                documented = _dedent_by(m.group("json"), m.group("indent"))
                out.append((path.relative_to(REPO_ROOT), line_no, pkg, argv, documented))
    return out


def _json_mismatch(expected, actual, where: str = "$") -> str | None:
    """Recursive compare; ``"..."`` on the documented side matches anything."""
    if expected == "...":
        return None
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{where}: documented an object, command printed {type(actual).__name__}"
        if set(expected) != set(actual):
            return f"{where}: keys differ — documented {sorted(expected)}, actual {sorted(actual)}"
        for key in expected:
            found = _json_mismatch(expected[key], actual[key], f"{where}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"{where}: documented an array, command printed {type(actual).__name__}"
        if len(expected) != len(actual):
            return f"{where}: documented {len(expected)} items, command printed {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            found = _json_mismatch(e, a, f"{where}[{i}]")
            if found:
                return found
        return None
    if expected != actual:
        return f"{where}: documented {expected!r}, command printed {actual!r}"
    return None


_JSON_EXAMPLES = _collect_json_examples()


@pytest.mark.slow
@pytest.mark.parametrize(
    "doc,line_no,package,argv,documented",
    _JSON_EXAMPLES,
    ids=[f"{doc}:{ln}" for doc, ln, _, _, _ in _JSON_EXAMPLES],
)
def test_documented_cli_json_output_matches(
    doc: Path, line_no: int, package: str, argv: list[str], documented: str, tmp_path: Path
) -> None:
    """A documented ``--json`` example must print the JSON the page shows.

    Runs the command through the package's own CLI entry point
    (``<pkg>.cli.app:app``, the same object the ``netstead`` / ``corral``
    console scripts call). The working directory is a scratch dir with
    ``packages/`` symlinked to the repo's, so the relative fixture paths
    in the docs resolve while any output the command writes (``corral
    convert … ./out.parquet``) lands in the scratch dir, not the repo.
    Compares parsed JSON, not text, so key order and indentation don't
    matter; ``"..."`` on the documented side is a wildcard for values
    that vary (timings, paths).
    """
    try:
        expected = json.loads(_BARE_ELLIPSIS_RE.sub('"..."', documented))
    except json.JSONDecodeError as exc:
        pytest.fail(f"Documented Expected JSON in {doc} (fence line {line_no}) does not parse: {exc}\n{documented}")

    (tmp_path / "packages").symlink_to(REPO_ROOT / "packages", target_is_directory=True)
    proc = subprocess.run(
        [sys.executable, "-c", f"from {package}.cli.app import app; app()", *argv],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    display = f"{package} {' '.join(argv)}"
    assert proc.returncode == 0, (
        f"Documented command failed (exit {proc.returncode}): {display}\n"
        f"  documented in: {doc}:{line_no}\n{proc.stderr}"
    )
    try:
        actual = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Command {display} printed non-JSON stdout ({exc}):\n{proc.stdout}")

    mismatch = _json_mismatch(expected, actual)
    if mismatch is not None:
        pytest.fail(
            f"Documented `Expected:` JSON does not match the command's output:\n"
            f"  command: {display}\n"
            f"  doc:     {doc} (fence line {line_no})\n"
            f"  {mismatch}\n\n"
            f"--- documented ---\n{documented}\n"
            f"--- actual stdout ---\n{proc.stdout}\n"
            f"Fix options:\n"
            f'  - Update the Expected block (use "..." for values that vary)\n'
            f"  - Mark the bash fence with `<!-- doctest: skip -->` if the\n"
            f"    example genuinely can't run in CI"
        )


def test_cli_map_built_successfully() -> None:
    """Smoke: at minimum we found commands on both apps.

    Catches the case where one of the typer apps fails to construct
    (import error, breaking refactor) — the parametrized test above
    would silently have zero cases, hiding the real failure.
    """
    assert _CLI_MAP["netstead"], "netstead app produced no commands"
    assert _CLI_MAP["corral"], "corral app produced no commands"
    # Spot-check known commands exist.
    assert ("validate",) in _CLI_MAP["netstead"]
    assert ("info",) in _CLI_MAP["netstead"]
    assert ("validate",) in _CLI_MAP["corral"]
    # And known flags.
    assert "--json" in _CLI_MAP["netstead"][("validate",)]
    assert "--html" in _CLI_MAP["netstead"][("validate",)]
