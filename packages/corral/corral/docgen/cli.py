"""Markdown reference for a click (or typer) command tree.

:func:`click_to_md` walks a :class:`click.Command` recursively and emits one
heading per command and sub-command with its help text, usage line,
arguments table and options table, so a docs site can generate its CLI
reference from the live command tree at build time instead of hand-writing
it. A typer app is converted first with ``typer.main.get_command(app)``.

Help text is taken raw from ``Command.help`` / ``Parameter.help`` (not from
rich's rendering, which swallows bracketed extras such as ``[clean]``) and
escaped for markdown by :func:`corral.docgen._text.escape_md`.

Examples:
    >>> import click
    >>> @click.group()
    ... def cli():
    ...     "A tiny tool."
    >>> @cli.command()
    ... @click.option("--json", "json_out", is_flag=True, help="Emit JSON.")
    ... def info(json_out):
    ...     "Show a summary."
    >>> md = click_to_md(cli, prog="tiny")
    >>> "## `tiny info`" in md and "`--json`" in md
    True
"""

from __future__ import annotations

import click

from ._text import escape_md, paragraphs

__all__ = ["click_to_md"]

#: Options every command gets from click's context rather than its signature.
_UNIVERSAL_OPTIONS = {"help"}


def _heading(level: int, text: str) -> str:
    return f"{'#' * level} {text}"


def _summary_and_body(help_text: str) -> tuple[str, list[str]]:
    """Split help into its first paragraph (the summary) and the remaining paragraphs."""
    parts = paragraphs(help_text)
    if not parts:
        return "", []
    return escape_md(parts[0]), parts[1:]


def _is_preformatted(para: str) -> bool:
    """A paragraph click marks "do not rewrap" (its no-rewrap marker), or one whose every line is indented."""
    if para.lstrip().startswith(("\\b", "\x08")):
        return True
    return all(line.startswith((" ", "\t")) for line in para.splitlines() if line.strip())


def _body_to_md(body: list[str]) -> list[str]:
    """Render the paragraphs after the summary.

    Preformatted paragraphs (see :func:`_is_preformatted`) go into one
    ``text`` block, consecutive ones merged, since their line breaks carry
    meaning (a mini table, an example invocation). Prose keeps its line
    breaks too, so an rst-style bullet list stays a list in markdown.
    """
    lines: list[str] = []
    pre: list[str] = []

    def _flush() -> None:
        if pre:
            lines.extend(["```text", "\n\n".join(pre), "```", ""])
            pre.clear()

    for para in body:
        if _is_preformatted(para):
            pre.append(para.replace("\\b", "").replace("\x08", "").strip("\n"))
        else:
            _flush()
            lines += [escape_md(para, collapse=False), ""]
    _flush()
    return lines


def _type_name(param: click.Parameter) -> str:
    ptype = param.type
    if isinstance(ptype, click.Choice):
        return "choice: " + " / ".join(f"`{c}`" for c in ptype.choices)
    if isinstance(param, click.Option) and param.is_flag:
        return "flag"
    return ptype.name


def _default(param: click.Parameter) -> str:
    default = param.default
    if default is None or default is Ellipsis or callable(default):
        return ""
    if isinstance(param, click.Option) and param.is_flag:
        return "" if not default else "on"
    if isinstance(default, list | tuple):
        default = ",".join(str(d) for d in default)
    return f"`{escape_md(str(default))}`" if str(default) != "" else ""


def _option_label(param: click.Option) -> str:
    names = list(param.opts) + list(param.secondary_opts)
    return ", ".join(f"`{n}`" for n in names)


def _argument_label(param: click.Argument) -> str:
    return f"`{param.human_readable_name}`"


def _params_tables(cmd: click.Command, ctx: click.Context) -> list[str]:
    arguments = [p for p in cmd.get_params(ctx) if isinstance(p, click.Argument)]
    options = [p for p in cmd.get_params(ctx) if isinstance(p, click.Option) and p.name not in _UNIVERSAL_OPTIONS]
    lines: list[str] = []
    if arguments:
        lines += ["**Arguments**", "", "| Argument | Type | Required | Help |", "|---|---|---|---|"]
        for arg in arguments:
            lines.append(
                f"| {_argument_label(arg)} | {_type_name(arg)} | {'yes' if arg.required else 'no'} "
                f"| {escape_md(getattr(arg, 'help', '') or '')} |"
            )
        lines.append("")
    if options:
        lines += ["**Options**", "", "| Option | Type | Default | Help |", "|---|---|---|---|"]
        for opt in options:
            lines.append(
                f"| {_option_label(opt)} | {_type_name(opt)} | {_default(opt)} | {escape_md(opt.help or '')} |"
            )
        lines.append("")
    return lines


def _takes_json(cmd: click.Command, ctx: click.Context) -> bool:
    return any("--json" in (p.opts or []) for p in cmd.get_params(ctx))


def _anchor(path: str) -> str:
    """The slug mkdocs' toc extension gives a heading whose text is ```path```."""
    return path.replace(" ", "-")


def _command_md(cmd: click.Command, ctx: click.Context, level: int) -> list[str]:
    """One command (or group) section, recursing into a group's sub-commands."""
    path = ctx.command_path
    summary, body = _summary_and_body(cmd.help or "")
    lines: list[str] = [_heading(level, f"`{path}`"), ""]
    if summary:
        lines += [summary, ""]
    lines += _body_to_md(body)
    lines += ["```text", cmd.get_usage(ctx).strip(), "```", ""]
    lines += _params_tables(cmd, ctx)
    if _takes_json(cmd, ctx):
        lines += ["Machine-readable: `--json` writes the result to stdout as JSON.", ""]
    if isinstance(cmd, click.Group):
        children = [(name, cmd.get_command(ctx, name)) for name in cmd.list_commands(ctx)]
        children = [(name, child) for name, child in children if child is not None and not child.hidden]
        if children:
            lines += ["**Sub-commands**", ""]
            for name, child in children:
                child_summary = _summary_and_body(child.help or "")[0]
                suffix = f" — {child_summary}" if child_summary else ""
                lines.append(f"- [`{path} {name}`](#{_anchor(f'{path} {name}')}){suffix}")
            lines.append("")
            for name, child in children:
                child_ctx = click.Context(child, info_name=name, parent=ctx)
                lines += _command_md(child, child_ctx, level + 1)
    return lines


def click_to_md(cmd: click.Command, *, prog: str, heading_level: int = 2) -> str:
    """Render ``cmd`` (usually a group) and every sub-command as markdown.

    Args:
        cmd: The root click command; for a typer app pass
            ``typer.main.get_command(app)``.
        prog: The program name shown in headings and usage lines
            (``"netstead"``).
        heading_level: Heading level for the top-level commands; each
            nesting level adds one.

    Returns:
        Markdown: the root's own options (if any), a linked index of the
        top-level commands, then one section per command. Sub-commands
        of a group follow the group's section one level deeper. Commands
        that accept ``--json`` say so in one line.
    """
    ctx = click.Context(cmd, info_name=prog)
    lines: list[str] = ["```text", cmd.get_usage(ctx).strip(), "```", ""]
    lines += _params_tables(cmd, ctx)
    if not isinstance(cmd, click.Group):
        return "\n".join(lines).rstrip() + "\n"
    children = [(name, cmd.get_command(ctx, name)) for name in cmd.list_commands(ctx)]
    children = [(name, child) for name, child in children if child is not None and not child.hidden]
    lines += ["| Command | What it does | `--json` |", "|---|---|---|"]
    for name, child in children:
        child_ctx = click.Context(child, info_name=name, parent=ctx)
        summary = _summary_and_body(child.help or "")[0]
        json_cell = (
            "yes" if _takes_json(child, child_ctx) else ("per sub-command" if isinstance(child, click.Group) else "")
        )
        lines.append(f"| [`{prog} {name}`](#{_anchor(f'{prog} {name}')}) | {summary} | {json_cell} |")
    lines.append("")
    for name, child in children:
        child_ctx = click.Context(child, info_name=name, parent=ctx)
        lines += _command_md(child, child_ctx, heading_level)
    return "\n".join(lines).rstrip() + "\n"
