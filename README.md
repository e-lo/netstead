# Netstead monorepo

Two Python packages for the [General Modeling Network Specification (GMNS)](https://github.com/zephyr-data-specs/GMNS), developed under the [Zephyr Foundation](http://zephyrtransport.org).

| Package | What it is | PyPI name | Docs |
|---|---|---|---|
| [`netstead`](packages/netstead/) | GMNS toolkit — build, load, validate, scope, edit GMNS networks, explore in a browser Workbench | `netstead` (release pending; [install from GitHub](#-v10-beta-is-open)) | [e-lo.github.io/netstead/netstead/](https://e-lo.github.io/netstead/netstead/) |
| [`corral`](packages/corral/) | Generic Frictionless Data Package engine that `netstead` builds on | `dbcorral` (release pending) | [e-lo.github.io/netstead/corral/](https://e-lo.github.io/netstead/corral/) |

Most users only install `netstead`. `corral` comes as a transitive dependency.

---

## 🧪 v1.0 beta is open

We're shipping the v1.0 rewrite as a public beta. **If you can spare half an hour to try it on a real network, your feedback shapes GA.**

The packages were renamed from `gmnspy` / `datagrove` in October 2026 and the first PyPI release under
the new names is pending, so install the beta from GitHub (both packages, in one command):

```bash
uv pip install \
  "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral" \
  "netstead[all] @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/netstead"
netstead doctor                        # confirm install
```

`pip install` and `uv add` accept the same two arguments. Once `netstead` 1.0.0b2 is on PyPI this
becomes `uv add 'netstead[all]==1.0.0b2'`.

- 📖 [Beta program details — what's in scope, how to report](BETA.md)
- 🐛 [File a beta-feedback issue](https://github.com/e-lo/netstead/issues/new?template=beta-feedback.md)
- 📝 [Migration from v0.3.x](packages/netstead/docs/migration/v0.3-to-v1.0.md)

---

## Quick start

```python
import netstead
from netstead.fixtures import leavenworth      # bundled example network

net = netstead.read(leavenworth.csv_dir())     # auto-detect format
report = netstead.validate(net)                # structural + schema + FK + sync
print(f"{net.links.count()} links, {len(report.issues)} validation issues")

report.to_html("report.html")                # interactive single-file HTML
```

More: the [5-minute quickstart](https://e-lo.github.io/netstead/netstead/quickstart/).

## Why a monorepo

`netstead` builds on a generic Frictionless engine (`corral`) extracted up front. The intent: future spec toolkits (GTFSpy, etc.) reuse the engine instead of reimplementing it. Both packages release independently; CI tests them together.

```
netstead/
├── packages/
│   ├── corral/   # generic engine — PyPI package
│   └── netstead/      # GMNS toolkit on top — PyPI package
├── skills/          # Claude Code Skills (copy into a project's .claude/skills/)
├── docs/            # umbrella landing page only — real docs are per-package
└── .github/         # CI/CD, issue templates, release-drafter
```

## Development

Workspace managed via [`uv`](https://docs.astral.sh/uv/). One command installs both packages with all extras editable:

```bash
git clone https://github.com/e-lo/netstead.git
cd netstead
uv sync --all-packages --all-extras

uv run netstead --help              # CLI
uv run pytest packages -n auto    # fast test tier (full: add -m ""; see CONTRIBUTING.md)
```

Full contributor workflow: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE).

## Acknowledgements

- [`zephyr-data-specs/GMNS`](https://github.com/zephyr-data-specs/GMNS) — upstream spec.
- [Zephyr Foundation](http://zephyrtransport.org) — project home.
- See [CONTRIBUTORS.md](CONTRIBUTORS.md).
