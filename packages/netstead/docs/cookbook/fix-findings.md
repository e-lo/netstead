---
title: Triage findings, propose fixes, apply them
audience: users
kind: howto
summary: Use the interactive viewer to propose fixes on individual validation findings, download them as a YAML edit log, and replay the log against your network in Python — with overwrite or save-as.
---

# Triage findings, propose fixes, apply them

## When to use this

You ran `netstead validate` and you have findings to address. You want to
triage them visually, propose fixes one at a time without writing
Python for each, then apply them deterministically and save. The flow
splits cleanly into two halves:

1. **Browser** — open the HTML report, click each finding's "Propose
   fix" button, accumulate an edit log.
2. **Python** — load the log, replay it against your network, save
   (overwrite or save-as).

The split lets you triage clickwise but always end up with a structured,
replay-able record of what changed.

## Quick example

```bash
# 1. Generate the validation report
uv run netstead validate ./my-net --html /tmp/report.html
open /tmp/report.html
```

In the browser:

- Each finding marker → click → popup with `Propose fix` button
- Click `Propose fix` → inline editor with the column pre-filled and
  the current value loaded → enter new value + reason → `Add to edit log`
- Bottom-right floating sidebar tracks your edits — `Download YAML`
  when done

Then back in Python:

<!-- doctest: skip -->
```python
from netstead import Network
from netstead.map.edits import apply_edits, load_edit_log

net = Network.from_source("./my-net")
log = load_edit_log("/path/to/edits-2026-06-30T19-00-00.yaml")
result = apply_edits(net, log)

print(result.summary())                # "12 applied, 2 skipped"
for s in result.skipped:               # inspect anything that didn't apply
    print(s.edit.id, "→", s.reason)

# Save either way — Network.write() takes a destination path. The format is
# inferred from the extension; a bare directory name writes Parquet, so pass
# format="csv" when you want to keep a CSV package.
result.net.write("./my-net", format="csv", overwrite=True)   # overwrite the CSV source
result.net.write("./my-net-v2")                              # new directory, Parquet
```

## The edit log shape

The download is a [network-wrangler ProjectCard](https://network-wrangler.github.io/projectcard/main/json-schemas/):
one card per session, with a top-level `changes:` array of
`roadway_property_change` entries. Each fix targets a single row via its
primary key (not positional row index — PKs survive row reordering
and partial reloads):

```yaml
project: netstead edits 2026-06-30T19:00:00.000Z
tags: [netstead, edit-log]
notes: |
  created_at: 2026-06-30T19:00:00.000Z
  client: netstead.map (browser)
changes:
  - roadway_property_change:
      facility:
        model_link_id: [42]
      property_changes:
        free_speed:
          existing: null            # netstead "from" — drift check on apply
          set: 25                   # netstead "to"
      notes: 'schema.required: free_speed missing · issue_id=i0 · edit_id=el7f9c3a'
```

**Interop with network-wrangler.** The file is a real ProjectCard; you
can pipe it into network-wrangler's own tooling. GMNS `link_id` maps to
`model_link_id` in the facility selector; per-cell values live under
`property_changes.<column>.set` with an optional `existing` drift
check. Node property changes emit with the same shape via
`model_node_id` — that's a netstead extension since standard
ProjectCard doesn't have a first-class node-property-change type;
network-wrangler may accept or reject depending on its version.

`existing:` is the value the browser-side editor *thought* was there
when you proposed the fix. `apply_edits` uses it for a drift check —
if the table's current value differs, the edit is skipped (with a
reason) rather than silently clobbering a concurrent change. Omit
`existing:` (or send `null`) to disable the check.

## Variations

??? note "Pass the log to a colleague (review workflow)"
    Don't apply anything locally. Download the YAML, email it (or open
    a PR with it). They run `apply_edits` against their copy of the
    network when they're ready.

??? note "Replay against a newer version of the same network"
    The vendor sent you v2 of the network. Run `apply_edits(v2_net, log)`
    — every edit whose PK still exists and whose `existing:` still matches
    applies cleanly. Anything that drifted shows up in `result.skipped`
    with a precise reason.

??? note "Bulk fixes from Python"
    `Edit` is a plain dataclass — build a `list[Edit]` programmatically
    ("every residential link with free_speed > 25 should be set to 25"),
    wrap it in an `EditLog`, and apply with the same `apply_edits` path.
    No browser needed. `pk` is a dict of primary-key columns, `to_value`
    the new cell value; `from_value` is optional and turns on the drift check.

    ```python
    import netstead
    from netstead.fixtures import leavenworth
    from netstead.map.edits import Edit, EditLog, apply_edits

    net = netstead.read(leavenworth.csv_dir())
    links = net.links.to_pandas()
    too_fast = links[(links.facility_type == "residential") & (links.free_speed > 25)].link_id

    edits = [
        Edit(id=f"cap-{lid}", kind="fix", table="link", pk={"link_id": int(lid)},
             column="free_speed", to_value=25.0, reason="residential cap")
        for lid in too_fast
    ]
    result = apply_edits(net, EditLog(edits=edits))
    print(result.summary())   # "271 applied, 0 skipped" on Leavenworth
    ```

??? note "Clear the log mid-session"
    The browser persists edits in `sessionStorage`. A page refresh
    keeps them. The sidebar's `Clear` button drops them (with a confirm
    prompt).

## Pitfalls

- **The browser-side log is per-session.** It lives in `sessionStorage`,
  so opening the report in a different tab gives you a fresh log. Always
  Download YAML before closing the tab if you want to keep the work.
- **Edits identify rows by PK, not row index.** That's deliberate — row
  index isn't stable across reloads. If your edit log targets
  `facility: {model_link_id: [42]}` and link_id 42 no longer exists in the
  destination network, the applier skips that edit with
  `pk {'link_id': 42} not found in 'link'`.
- **Drift checks compare values pre-cast.** When the source column is a
  number, the editor coerces the new value to a number too. When in
  doubt, look at the YAML — `existing: 40` vs `existing: '40'` matter for
  the strict drift check (only null and NaN are treated as the same
  "missing" value).

## See also

* [View a network on a map](view-your-network.md) — the embedding the
  edit-log UI lives on top of.
* [Validate a network](validate-network.md) — produces the findings the
  Propose-fix flow targets.
* `netstead.map.NetworkMap` — the embeddable component itself.
