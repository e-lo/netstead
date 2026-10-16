"""Measure how accurately a model plans multi-step Workbench requests (run by hand, never in CI).

    uv run --all-extras python scripts/eval_nl_assistant.py --provider ollama [--model qwen2.5:7b] [--runs 3]

Each request in ``scripts/data/nl_assistant_eval_set.toml`` is planned ``--runs`` times on the Leavenworth fixture
through the same ``Assistant.plan`` the Workbench uses (tools, grounding, repairs), and nothing it plans is
dispatched (only the case's own ``select_first`` selection is). A plan is "correct" when its steps have the expected
Action types in order and every expected field matches; "valid" when the model's answer checked out at all (after
any repairs). It prints a per-request table, totals (correct, valid, repairs, keys left out,
tokens) and the misses.

Options left out follow your netstead settings. It calls whichever provider you name: a remote provider spends real
tokens (runs x requests calls, plus repairs). With a local Ollama, grounding is on by default, as in the app.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session

EVAL_SET = Path(__file__).resolve().parent / "data" / "nl_assistant_eval_set.toml"


def load_cases(path: Path) -> list[dict[str, Any]]:
    """The eval cases, each with ``utterance``, ``expect`` (a list) and optional ``select_first`` / ``no_calls``."""
    cases = tomllib.loads(path.read_text())["case"]
    for case in cases:
        case.setdefault("expect", [])
    return cases


def _norm(value: Any) -> Any:
    return re.sub(r"[\s\-]+", "", value).casefold() if isinstance(value, str) else value


def _get(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def check(got: list[dict[str, Any]], expect: list[dict[str, Any]]) -> list[str]:
    """What is wrong with the planned steps ``got`` (Action dumps) against ``expect`` (empty when correct)."""
    if len(got) != len(expect):
        return [f"{len(got)} step(s), expected {len(expect)}"]
    errors = []
    for i, (step, want) in enumerate(zip(got, expect, strict=True), start=1):
        if step["type"] != want["type"]:
            errors.append(f"step {i}: {step['type']}, expected {want['type']}")
            continue
        for key, value in want.items():
            if key != "type" and _norm(_get(step, key)) != _norm(value):
                errors.append(f"step {i}: {key}={_get(step, key)!r}, expected {value!r}")
    return errors


@dataclass
class EvalResult:
    """Per request ``(utterance, correct, valid, mean seconds)``, the misses, and the totals."""

    runs: int
    rows: list[tuple[str, int, int, float]] = field(default_factory=list)
    misses: list[tuple[str, str]] = field(default_factory=list)
    tokens: int = 0
    repairs: int = 0
    ignored: int = 0  # keys left out of read-only calls (Plan.ignored)
    dispatched: int = 0  # Actions the assistant dispatched: always 0 (planning only)


def run_eval(
    cases: list[dict[str, Any]],
    *,
    runs: int,
    overrides: Mapping[str, Any] | None = None,
    provider: Any = None,
    environ: Mapping[str, str] | None = None,
    project_dir: str | Path | None = None,
) -> EvalResult:
    """Plan every case ``runs`` times on Leavenworth; ``provider`` (tests) replaces the one the settings name."""
    source = str(leavenworth.parquet_dir())
    settings = {"io.allowed_roots": [source], **(overrides or {})}
    out = EvalResult(runs)
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(
            project_dir=project_dir or tmp,
            overrides=settings,
            parser=StubParser(),
            plugins=[],
            environ=environ,
            assistant_provider=provider,
        )
        session.dispatch({"type": "open_network", "source": source})
        if provider is None and session.settings.select.provider == "ollama":
            _probe_ollama(session)
        for case in cases:
            ok = valid = 0
            seconds = 0.0
            for _ in range(runs):
                if case.get("select_first"):
                    session.dispatch({"type": "select", "link_ids": case["select_first"]})
                else:
                    session.dispatch({"type": "clear_selection"})
                start = time.monotonic()
                try:
                    plan = session.assistant.plan(case["utterance"])
                except Exception as exc:  # boundary: a failed plan is a miss, reported below
                    out.misses.append((case["utterance"], f"{type(exc).__name__}: {exc}"))
                    continue
                finally:
                    seconds += time.monotonic() - start
                valid += 1
                out.tokens += plan.tokens
                out.repairs += plan.attempts - 1
                out.ignored += len(plan.ignored)
                got = [a.model_dump(mode="json") for a in plan.actions]
                problems = ["expected no steps"] if case.get("no_calls") and got else check(got, case["expect"])
                ok += not problems
                if problems:
                    out.misses.append((case["utterance"], "; ".join(problems)))
            out.rows.append((case["utterance"], ok, valid, seconds / max(runs, 1)))
        out.dispatched = sum(1 for h in session.history if h.via == "assistant")
    return out


def _probe_ollama(session: Session) -> None:
    """Exit with a clear message, before any case runs, when the Ollama server can't be reached."""
    from netstead.llm import LLMError

    try:
        session.llm.provider("ollama").list_models()
    except LLMError as exc:
        url = session.settings.llm.ollama.base_url
        raise SystemExit(f"Ollama isn't reachable at {url} ({exc}); start it with `ollama serve`") from None


def report(result: EvalResult) -> str:
    """The table, the totals and the misses, as text."""
    runs = result.runs
    lines = [f"{'request':64} correct  valid  s/plan"]
    for utterance, ok, valid, secs in result.rows:
        lines.append(f"{utterance[:64]:64} {ok:>4}/{runs} {valid:>4}/{runs} {secs:7.2f}")
    total = sum(r[1] for r in result.rows)
    valid = sum(r[2] for r in result.rows)
    n = len(result.rows) * runs
    lines.append("")
    lines.append(
        f"correct {total}/{n} ({100 * total / max(n, 1):.0f}%), valid {valid}/{n}, repairs {result.repairs}, "
        f"keys left out {result.ignored}, "
        f"tokens {result.tokens:,}, dispatched by the assistant {result.dispatched}"
    )
    lines += [f"  MISS {utterance!r}: {why}" for utterance, why in result.misses]
    return "\n".join(lines)


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--provider", required=True, help="ollama, anthropic, openai or gemini")
    parser.add_argument("--model", help="model id (default: the one selections use)")
    parser.add_argument("--runs", type=int, default=3, help="plans per request (default 3)")
    parser.add_argument("--eval-set", type=Path, default=EVAL_SET)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run the eval and print the table; exit 0 (it measures, it doesn't gate)."""
    args = _args(argv)
    overrides = {"select.provider": args.provider, **({"select.model": args.model} if args.model else {})}
    print(report(run_eval(load_cases(args.eval_set), runs=args.runs, overrides=overrides)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
