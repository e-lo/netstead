"""Contract test: every Python code block in our docs must actually run.

**Why this test exists.** The Python-API contract test
(``test_documented_api_contract.py``) catches references to symbols
that don't exist. The CLI contract test
(``test_documented_cli_contract.py``) catches references to CLI flags
that don't exist. Neither catches the third doc-vs-code drift class —
a code block that uses real symbols + real flags but calls them with
wrong arguments, wrong shapes, or in the wrong order.

The pre-beta review on 2026-05-27 found a dozen examples of this
class in cookbook + quickstart pages: `report.passed`, `i.is_error()`,
`net.validate(passes=[...])`, `from_point(lon=, lat=)`,
`report.to_html("path.html")`, and so on. None of these would be
caught by the symbol/flag tests — they exec-fail at runtime.

This test closes that gap. It walks every ``.md`` doc, extracts every
``python``-tagged fenced block, executes each in a fresh namespace,
and reports failures with line numbers.

**Scope decisions:**

- We only execute blocks tagged ``python`` (not pseudocode or ``py``
  for inline snippets).
- Indented fences count too: a ```python fence inside a ``???`` /
  ``!!!`` admonition or a ``=== "tab"`` is collected, and its body is
  dedented by exactly the fence's indentation (internal indentation
  is preserved). Blockquote-wrapped fences (``> ```python``) are still
  ignored — they're illustrative.
- We **skip** blocks marked with the HTML comment
  ``<!-- doctest: skip -->`` immediately before the fence (escape
  hatch for examples that need network access, real S3 creds, or a
  GUI). The marker may itself be indented, and a blank line between
  marker and fence is fine.
- When a block is immediately followed (ignoring blank lines) by a
  line reading ``Expected:`` and a ```text / ```json fence, the
  block's captured stdout must match that fence: trailing whitespace
  is ignored per line, a documented line ending in ``...`` (or ``…``)
  is a prefix match, and the documented block may stop early (it may
  show only the first lines of the output). Nondeterministic values
  (timings, temp paths) must be elided with ``...`` by the author.
- We **skip** the migration guide (it deliberately shows OLD v0.3
  API that must NOT exist in v1.0).
- We **skip** generated artifacts (``llms*.txt``, ``api-index.json``).
- We **skip** assert-style blocks that depend on user input (we
  detect ``input(`` calls and skip those).
- We run blocks in a per-file namespace so common imports established
  in early blocks carry through later ones in the same page (the
  natural way a user reads the page top-to-bottom).
- Each block is wrapped in ``contextlib.redirect_stdout`` so prints
  don't pollute pytest output unless the block raises.

When this test goes red on a doc page, the failure message includes
the file, line of the fence, and the full traceback — the same
debugging info the user would see, minus their muscle memory.
"""

from __future__ import annotations

import contextlib
import io
import re
import textwrap
import warnings
from pathlib import Path

import pytest

# Executes every doc example end to end (~6s serial): a pre-merge check, not an iteration loop.
pytestmark = pytest.mark.slow

# Repo layout: packages/netstead/tests/test_documented_python_contract.py
REPO_ROOT = Path(__file__).resolve().parents[3]

_DOC_GLOBS = [
    "packages/corral/README.md",
    "packages/corral/docs/**/*.md",
    "packages/netstead/README.md",
    "packages/netstead/docs/**/*.md",
    "README.md",
    "BETA.md",
]

# Fenced ```python blocks, at column 0 or indented (inside a `???` /
# `!!!` admonition or a `=== "tab"`). The opening fence's leading
# whitespace is captured as `indent`; the closing fence must carry the
# same indentation. The body is dedented by exactly that indent in
# `_collect_blocks`. The directive form `.. code-block:: python` (RST
# inside markdown) is rare in our docs but we handle it too.
#
# Only whitespace may precede the fence, so blockquote-wrapped
# examples (`> `​``python ... `​``) — the page-style-guide pattern —
# are ignored entirely. Their inner content isn't meant to exec.
_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)```python[ \t]*\n(?P<body>.*?)\n(?P=indent)```[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)

# An `Expected:` output block directly after a python fence (blank
# lines allowed in between). Matched against the text that FOLLOWS a
# `_FENCE_RE` match, so it must start right after the closing fence.
_EXPECTED_RE = re.compile(
    r"[ \t]*\n(?:[ \t]*\n)*"
    r"(?P<indent>[ \t]*)Expected:[ \t]*\n(?:[ \t]*\n)*"
    r"(?P=indent)```(?:text|json)[ \t]*\n(?P<body>.*?)\n(?P=indent)```[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)
_RST_BLOCK_RE = re.compile(
    r"\.\.\s+code-block::\s+python\s*\n((?:[ \t]+:[\w-]+:.*\n)*)\n((?:[ \t]+.*\n?)+)",
    flags=re.MULTILINE,
)

# Inline marker placed in markdown to skip a fenced block. Example:
#     <!-- doctest: skip -->
#     ```python
#     ...code that needs S3 creds...
#     ```
_SKIP_MARKER = re.compile(r"^\s*<!--\s*doctest:\s*skip\s*-->\s*$")


def _block_is_skipped(text: str, fence_start: int) -> bool:
    """Is there a skip marker among the few non-blank lines before the fence?

    Walks backwards from the fence, ignoring blank lines, and looks at
    up to three non-blank lines (enough for ``marker`` / ``marker +
    admonition title`` / ``marker + blank + fence``). Stops at any
    fence line so a marker on an earlier block can't leak onto this
    one. The marker may be indented.
    """
    preceding = text[:fence_start].splitlines()
    seen = 0
    for line in reversed(preceding):
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
    """Strip exactly ``indent`` from every line that carries it.

    Blank lines become empty; a line that does not start with the
    indent is left as-is so a malformed block fails loudly at exec.
    """
    if not indent:
        return body
    out: list[str] = []
    for line in body.split("\n"):
        if line.startswith(indent):
            out.append(line[len(indent) :])
        elif not line.strip():
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def _collect_blocks() -> list[tuple[Path, int, str, str | None]]:
    """Walk every doc, return [(path, line_no, body, expected), ...].

    ``expected`` is the dedented body of the ```text / ```json fence
    that follows an ``Expected:`` line right after the block, or
    ``None`` when the block documents no output.
    """
    blocks: list[tuple[Path, int, str, str | None]] = []
    for glob in _DOC_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if path.name.startswith("llms") or path.name == "api-index.json":
                continue
            # Skip the migration guide — it deliberately quotes OLD v0.3
            # API as the "before" side of the migration table.
            if path.name == "v0.3-to-v1.0.md":
                continue
            text = path.read_text(encoding="utf-8")
            for match in _FENCE_RE.finditer(text):
                if _block_is_skipped(text, match.start()):
                    continue
                # Block is "real". Compute line number of the fence's
                # first line (the ```python line, 1-indexed).
                line_no = text[: match.start()].count("\n") + 1
                indent = match.group("indent")
                body = _dedent_by(match.group("body"), indent)
                # Skip blocks that need user input.
                if "input(" in body:
                    continue
                expected: str | None = None
                after = _EXPECTED_RE.match(text, match.end())
                if after is not None:
                    expected = _dedent_by(after.group("body"), after.group("indent"))
                blocks.append((path.relative_to(REPO_ROOT), line_no, body, expected))
    return blocks


def _compare_expected(expected: str, actual: str) -> str | None:
    """Return a mismatch description, or ``None`` when ``actual`` matches.

    Rules: trailing whitespace is ignored per line (and trailing blank
    lines are dropped); a documented line ending in ``...`` or ``…`` is
    a prefix match on the text before the ellipsis; the documented
    block may be a prefix of the output (fewer lines is fine, more is
    not). No numeric tolerance — exact text otherwise.
    """

    def _lines(s: str) -> list[str]:
        lines = [ln.rstrip() for ln in s.splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        return lines

    exp, act = _lines(expected), _lines(actual)
    if len(exp) > len(act):
        return f"documented output has {len(exp)} lines but the block printed {len(act)}"
    for i, (e, a) in enumerate(zip(exp, act, strict=False), start=1):
        for ellipsis in ("...", "…"):
            if e.endswith(ellipsis):
                prefix = e[: -len(ellipsis)].rstrip()
                if not a.startswith(prefix):
                    return f"line {i}: documented prefix {prefix!r} but block printed {a!r}"
                break
        else:
            if e != a:
                return f"line {i}: documented {e!r} but block printed {a!r}"
    return None


def _is_obviously_pseudocode(body: str) -> bool:
    """Heuristic: skip blocks that are clearly outline/pseudocode.

    We don't want to false-positive on snippets like ``net.foo(...)``
    where the ``...`` is a literal placeholder. These usually appear
    inside admonitions explaining shape, not executable recipes. The
    heuristic catches:

    * Empty blocks.
    * Blocks that ONLY contain ``...`` placeholders or comments.
    * Blockquote-wrapped blocks (every line starts with ``> ``). Common
      in the page-style guide where blocks demonstrate "what good code
      should look like" inside a blockquote — they're not meant to exec.
    """
    stripped = body.strip()
    if not stripped:
        return True
    # All-ellipsis or all-comment bodies.
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if all(ln in ("...", "# ...") or ln.startswith("# ") for ln in lines):
        return True
    # Blockquote-wrapped blocks — every non-blank source line starts
    # with `> ` (markdown blockquote marker). exec would choke on the
    # `>` characters; the block is illustrative, not runnable.
    source_lines = [ln for ln in body.splitlines() if ln.strip()]
    return bool(source_lines) and all(ln.lstrip().startswith("> ") for ln in source_lines)


# Per-file namespace cache so subsequent blocks see earlier imports.
# Pytest parametrizes once per (path, line_no) tuple but we want a
# shared namespace per path — a module-level dict keyed by path does it.
_NAMESPACES: dict[Path, dict] = {}


def _blocks_for_parametrize() -> list[tuple[Path, int, str, str | None]]:
    out = []
    for path, line, body, expected in _collect_blocks():
        if _is_obviously_pseudocode(body):
            continue
        out.append((path, line, body, expected))
    return out


_BLOCKS = _blocks_for_parametrize()

# Known doc-vs-code gaps that the pre-beta review surfaced and are
# tracked for a follow-up cookbook-sweep PR. Each entry is (path,
# line_no) so the test xfails specifically rather than skipping. Remove
# the entry once the underlying doc is fixed and the block runs.
#
# This list is the punch-list for the v1.0-beta cookbook sweep. As
# each block is fixed, remove its entry — the strict assertion takes
# over and prevents regression. Net-zero entries by GA.
#
# Format: {(relative_path_str, fence_line_no): "reason"}
_KNOWN_BROKEN: dict[tuple[str, int], str] = {}


@pytest.fixture
def _isolate_global_state():
    """Snapshot + restore process-global state mutated by doc blocks.

    Two kinds of global state leak across doc blocks (each runs as a
    separate parametrized test, but globals persist for the whole
    pytest session):

    1. **Quality rule registry** — doc blocks that exec
       ``register_rule(...)`` (the customise-quality cookbook) would
       pollute later tests that expect a clean registry.
    2. **ibis interactive mode** — the query-and-update cookbook shows
       the natural notebook pattern ``ibis.options.interactive = True``
       (set once, leave on). Left on, *every* later block renders
       eagerly, and a join expression under interactive repr trips
       ``TypeError: unhashable type: 'list'`` in ibis. Snapshot +
       restore so each block starts from the real default.

    Same snapshot/restore dance as ``tests/io/conftest.py``'s
    adapter-registry fixture.
    """
    import ibis
    from corral.quality import registry as _qreg

    qsnapshot = dict(_qreg._registry)
    qdiscovered = _qreg._discovered
    interactive = ibis.options.interactive
    try:
        yield
    finally:
        _qreg._registry.clear()
        _qreg._registry.update(qsnapshot)
        _qreg._discovered = qdiscovered
        ibis.options.interactive = interactive


@pytest.mark.parametrize(
    "path,line_no,body,expected",
    _BLOCKS,
    ids=[f"{p}:{ln}" for p, ln, _, _ in _BLOCKS],
)
def test_documented_python_block_runs(
    path: Path, line_no: int, body: str, expected: str | None, _isolate_global_state: None
) -> None:
    """Every ```python fenced block in our docs must exec without raising.

    Closes the doc-vs-code drift class the symbol + flag contract
    tests don't catch — calls with wrong args, wrong shapes, or wrong
    method names that pass type/symbol resolution but raise at runtime.

    When this fails, the traceback is the SAME thing a user
    copy-pasting the example would see, with one indirection (the
    pytest harness). Fix the doc block, the test goes green, the
    next reader gets a working example.

    When the block is followed by ``Expected:`` and a ```text / ```json
    fence, the block's stdout must match it (see ``_compare_expected``
    for the rules) — the page must say what the code really prints.

    Skip an unreliable block with ``<!-- doctest: skip -->`` on the
    line immediately before the fence. Reserve for blocks that need
    network access, real cloud creds, a GUI, or other genuine
    non-determinism — not "this is broken and I'd rather not fix it".
    """
    key = (str(path), line_no)
    if key in _KNOWN_BROKEN:
        pytest.xfail(f"known doc-vs-code gap: {_KNOWN_BROKEN[key]}")

    # The fence indent is already stripped; dedent again in case the
    # whole body sits deeper than its fence (a list item, say).
    code = textwrap.dedent(body)

    # Shared namespace per file so an earlier `import netstead` carries.
    ns = _NAMESPACES.setdefault(path, {"__name__": f"__doctest__.{path.name}"})

    # Suppress prints, warnings (pyplot etc), but let exceptions escape.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            exec(compile(code, str(path), "exec"), ns)
    except Exception as exc:
        pytest.fail(
            f"Documented Python block raised at exec time:\n"
            f"  file:  {path}\n"
            f"  fence: line {line_no}\n"
            f"  error: {type(exc).__name__}: {exc}\n\n"
            f"--- block body ---\n{code}\n"
            f"--- stdout so far ---\n{buf.getvalue() or '(empty)'}\n\n"
            f"Fix options:\n"
            f"  - Update the doc block to call the real API\n"
            f"  - Mark with `<!-- doctest: skip -->` if it genuinely needs\n"
            f"    network/cloud creds/GUI/non-determinism\n"
            f"  - Add ({path!s}, {line_no}) to _KNOWN_BROKEN in\n"
            f"    {Path(__file__).name} with a tracking reason"
        )

    if expected is None:
        return
    mismatch = _compare_expected(expected, buf.getvalue())
    if mismatch is not None:
        pytest.fail(
            f"Documented `Expected:` output does not match what the block prints:\n"
            f"  file:  {path}\n"
            f"  fence: line {line_no}\n"
            f"  {mismatch}\n\n"
            f"--- documented ---\n{expected}\n"
            f"--- actual stdout ---\n{buf.getvalue() or '(empty)'}\n"
            f"Fix options:\n"
            f"  - Update the Expected block to what the code really prints\n"
            f"  - End a line with `...` where the value varies (prefix match)\n"
            f"  - Mark the block with `<!-- doctest: skip -->` if its output\n"
            f"    is genuinely nondeterministic"
        )
