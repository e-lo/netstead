# Netstead + corral v1.0 — beta program

Thanks for trying the v1.0 beta. This page tells you what the beta is, what to expect, and how to get the most useful feedback to us.

## What's in the beta

Two Python packages, released together:

| Package (PyPI) | Import | Beta tag | What it is |
|---|---|---|---|
| **`dbcorral`** | `corral` | `corral-v1.0.0-beta.2` | Generic Frictionless Data Package engine — lazy DuckDB (via ibis), validation, scope, edit/rollback. |
| **`netstead`** | `netstead` | `netstead-v1.0.0-beta.2` | GMNS-specific toolkit on top — quality rules, network-aware scope, clean/edit, NL selection, interactive viewer, HTTP server, MCP. |

Most users only install `netstead`. `dbcorral` comes as a transitive dependency. The two
packages version in lockstep.

> `beta.2` is the first beta under these names. `beta.1` was tagged as
> `gmnspy-v1.0.0-beta.1` / `datagrove-v1.0.0-beta.1` before the 2026-10-05 rename.

## Install the beta

The first PyPI release under the new names is pending, so the beta installs from GitHub. Name both
packages in one command (`dbcorral` cannot be resolved from PyPI yet, so it has to be given explicitly):

=== "uv (recommended)"

    ```bash
    uv init --bare
    uv add \
      "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral" \
      "netstead[all] @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/netstead"
    ```

=== "uv pip / pip"

    ```bash
    pip install \
      "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral" \
      "netstead[all] @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/netstead"
    ```

`uv init --bare` makes a new folder a uv project, which `uv add` needs; skip it when you add netstead to a project you already have.

Once `netstead` 1.0.0b2 is on PyPI these become `uv add 'netstead[all]==1.0.0b2'` /
`pip install 'netstead[all]==1.0.0b2'` / `pipx install 'netstead[all]==1.0.0b2'`.

See the [install guide](https://e-lo.github.io/netstead/netstead/install/) for extras + the `zsh` quoting note.

After install, confirm everything works:

```bash
netstead doctor
```

That should report all green. If anything fails, [file a beta-feedback issue](#how-to-report) with the doctor output.

## Try it on the bundled fixture

A small real network (Leavenworth, WA — the whole city, OSM-derived) ships in the wheel so you can try things without finding data:

```bash
# Print the bundled-fixture path
LV=$(uv run python -c "from netstead.fixtures import leavenworth; print(leavenworth.csv_dir())")

# Walk the basic surface
uv run netstead info $LV
uv run netstead validate $LV --html /tmp/lv-report.html && open /tmp/lv-report.html
uv run netstead quality $LV
```

Or in Python:

```python
import netstead
from netstead.fixtures import leavenworth

net = netstead.read(leavenworth.csv_dir())            # netstead.Network
report = netstead.validate(net)                       # ValidationReport
print(f"{net.links.count()} links, {len(report.issues)} issues")
```

## What we want from beta users

In rough priority order:

1. **Wrong-answer bugs.** A command exits 0 but the result is wrong. These are the hardest to catch in CI and the most important to find before GA.
2. **"It crashed loading my data."** Pull / strip / synthesise the shape of your data, file with a reproduction. Even an env mismatch report is useful.
3. **"I expected X to do Y but it did Z."** Friction. Where our docs say one thing and the code does another. Where the error message doesn't tell you what to fix.
4. **First-touch experience.** Did you install it from cold, run the quickstart, and have everything work? Where did you stumble?
5. **Performance at your network's scale.** Run `netstead bench /path/to/your/network` and tell us the wall-time numbers.

Less critical:

6. Suggestions for v1.1+ features.
7. "Have you considered building X?" — yes; we have a roadmap. File anyway, but expect "after GA."

## What's IN scope for beta fixes

- Wrong-answer bugs (any severity).
- Crashes on real-world data shapes.
- Documented behavior that's actually missing or different.
- Missing error messages / unhelpful tracebacks.
- Docs gaps where a user genuinely couldn't proceed.

## What's OUT of scope for beta fixes (will land in v1.1 or later)

- New features (unless trivial).
- Performance refactors beyond fixing pathological regressions.
- API surface additions (we want the v1.0 surface to settle, not grow).
- Windows support (community-maintained only).

## How to report

[**Open a beta-feedback issue →**](https://github.com/e-lo/netstead/issues/new?template=beta-feedback.md)

The template asks for:

1. What you tried to do (one sentence).
2. What happened.
3. The shortest reproduction we can run.
4. The output of `netstead doctor --json`.

You don't have to fill every section — anything beats a silent fail.

## What we don't promise during beta

- Turnaround SLAs. This is volunteer time.
- API stability across `beta.N` → `beta.N+1`. We'll try to avoid breaking changes but the surface may shift if early users find sharp edges.
- Patches against `beta.N` once `beta.N+1` is out. Upgrade.

## When does it ship?

We're targeting GA (`corral-v1.0.0` + `netstead-v1.0.0`) **after at least two `beta.N` cycles** with no new critical bugs reported in the most recent cycle. Realistic timeline: 4–8 weeks from `beta.1`, depending on what beta finds.

## Roadmap beyond v1.0

Planned and in-flight work is indexed in [`docs/design/README.md`](docs/design/README.md).
Likely post-GA candidates (not promised):

- The **Workbench** (`netstead app`) — one front end for open / select / inspect / edit, with a plugin API.
- More network-cleanup ops (`split_link_at_node`, `snap_to_reference` — [#152](https://github.com/e-lo/netstead/issues/152)).
- Polygon and CRS-aware scope operations.
- More quality rules (community contributions welcome).

## Thank you

Beta-program participation is unpaid open-source work, and it makes the GA release massively better. If you find a real issue, you'll get credit in the v1.0.0 changelog acknowledgements.

— Elizabeth Sall + maintainers
