# Netstead monorepo

Two Python packages for the [General Modeling Network Specification (GMNS)](https://github.com/zephyr-data-specs/GMNS), developed under the [Zephyr Foundation](http://zephyrtransport.org).

| Package | What it is | PyPI | Docs |
|---|---|---|---|
| [`netstead`](packages/netstead/) | GMNS toolkit — build, load, validate, scope, edit GMNS networks, explore in a browser Workbench | [`netstead`](https://pypi.org/project/netstead/) | [e-lo.github.io/netstead/netstead/](https://e-lo.github.io/netstead/netstead/) |
| [`corral`](packages/corral/) | Generic Frictionless Data Package engine that `netstead` builds on | [`dbcorral`](https://pypi.org/project/dbcorral/) | [e-lo.github.io/netstead/corral/](https://e-lo.github.io/netstead/corral/) |

Most users only install `netstead`. `corral` comes as a transitive dependency.

---

## 🧪 v1.0 beta is open

We're shipping the v1.0 rewrite as a public beta. **If you can spare half an hour to try it on a real network, your feedback shapes GA.**

```bash
uv add 'netstead[all]==1.0.0b2'        # or: pip install 'netstead[all]==1.0.0b2'
netstead doctor                        # confirm install
```

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
