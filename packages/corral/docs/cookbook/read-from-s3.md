---
title: Read from S3 with credentials
audience: users
kind: howto
summary: Load a Frictionless data package from S3 (or any fsspec-backed cloud store) with the credential cascade — kwarg, env var, keyring, .netrc.
---

# Read from S3 with credentials

## When to use this

Your data lives on cloud storage and you want the same lazy-load surface you get from a local directory — predicate pushdown, column pruning, no full download. Credentials should come from whichever channel your environment already manages (CI secret, keyring, `~/.netrc`) without ever being baked into source.

## Quick example

Open a package directly from S3 by URL. If nothing in the credential cascade resolves, `s3fs` falls back to the standard AWS credential chain (env vars, `~/.aws/credentials`, IAM role) — you only pass `credentials=` when you need to override it.
<!-- doctest: skip -->

```python
from corral import read

pkg = read("s3://my-bucket/networks/leavenworth/")
print(f"{len(pkg.tables)} tables, {pkg['link'].count()} links")
```

Expected (will vary by package):

```text
9 tables, 339 links
```

If your bucket is public-readable, no credential resolution runs at all. Either way the load is lazy — `pkg['link']` is a `Table` wrapping an ibis expression, not a materialised frame.

## Step-by-step

### 1. Install

Cloud filesystem drivers come from the matching `dbcorral` extra — `s3` (`s3fs`), `gcs` (`gcsfs`), `azure` (`adlfs`). If you hit `ImportError: No module named 's3fs'`, that's the missing extra:

```bash
pip install 'dbcorral[s3]'      # or [gcs] / [azure]; combine with commas
```

### 2. Provide credentials

Credentials cascade in a fixed order. The first one resolved wins:

1. **Explicit kwarg** — `Package.from_source(url, credentials={...})` / `corral.read(url, credentials={...})`. Always wins. Use for one-off scripts and notebooks.
2. **Environment variables** — `CORRAL_CRED_<HOST>_TOKEN` for a bearer token, or `CORRAL_CRED_<HOST>_KEY` + `CORRAL_CRED_<HOST>_SECRET` for S3-style key pairs. `<HOST>` is the URL host uppercased with dots and dashes flattened to underscores — for `s3://my-bucket/...` that's `CORRAL_CRED_MY_BUCKET_KEY` / `_SECRET`. Use for CI.
3. **System keyring** — service name `corral`, username matches host. Use for local dev so a token never lands in your shell history.
4. **`~/.netrc`** — falls back to the standard netrc machine entry. Use for legacy compatibility.

When no layer produces anything, corral hands `s3fs` an empty options dict and the AWS SDK's own chain takes over — IAM role, `~/.aws/credentials`, `AWS_*` env vars. You only need `CORRAL_CRED_*` variables when the bucket needs a non-default credential (e.g. a different AWS account).

To store a credential in the keyring on a dev machine — once, interactively — call the keyring API directly:
<!-- doctest: skip -->

```python
import keyring
keyring.set_password("corral", "my-bucket", "token-here")
```

The same call from `python -c` works for CI bootstrapping if you'd rather not put the secret in an env file.

### 3. Load the package

Discovery via the cascade is the common path. An explicit kwarg overrides discovery, which is handy in notebooks where you'd rather not depend on shell environment:
<!-- doctest: skip -->

```python
from corral import Package

# Discover via the cascade above:
pkg = Package.from_source("s3://my-bucket/networks/leavenworth/")

# Or pass explicit credentials (kwarg always wins):
pkg = Package.from_source(
    "s3://my-bucket/networks/leavenworth/",
    credentials={"key": "AKIA…", "secret": "…"},
)
```

The `credentials` dict is forwarded as fsspec `storage_options` for the URL's filesystem, so any key `s3fs` accepts works. The result is the same `Package` you'd get from a local path. Every table (`pkg['link']`, `pkg['node']`, …) is a lazy `Table` backed by the cloud-aware reader.

### 4. Verify the load is lazy

Inspect the underlying expression and confirm it's an ibis `Table`. Then push a filter down — `Table.filter` takes a callable over the ibis expression — and materialise only the survivors (`to_polars()` needs the `polars` extra; `to_pandas()` the `pandas` one):
<!-- doctest: skip -->

```python
expr = pkg["link"].expr   # underlying ibis Table
print(type(expr).__name__)  # 'Table'

fast_links = pkg["link"].filter(lambda t: t.filter(t.free_speed > 45.0)).to_polars()
```

Filters and column projections push down to the parquet readers, so a large package over S3 can return a small filtered frame without downloading the whole file.

### 5. Cache for repeat reads

If the same script will hit the package many times — a notebook, a debugging loop, a CI matrix — convert it once and load locally afterwards:
<!-- doctest: skip -->

```python
from corral import Package

remote = Package.from_source("s3://my-bucket/networks/leavenworth/")
remote.write("/tmp/leavenworth.parquet")   # one-shot download; raises FileExistsError if it already exists

# Subsequent runs (no manifest is written — pass the spec again if you had one):
pkg = Package.from_source("/tmp/leavenworth.parquet")
```

Parquet is faster to re-read than CSV-over-S3 by an order of magnitude on cold cache. See [Convert formats](convert-formats.md) for the format trade-offs.

## Common variations

???+ note "S3 (default) — AWS chain or `CORRAL_CRED_*_KEY` + `_SECRET`"
    Requires `pip install 'dbcorral[s3]'` (brings in `s3fs`). With no corral credentials resolved, the AWS SDK chain handles standard auth automatically.
<!-- doctest: skip -->

    ```python
    pkg = Package.from_source("s3://my-bucket/networks/leavenworth/")
    ```

??? note "HTTPS sources"
    Plain `https://` URLs go through fsspec's HTTP filesystem. A `CORRAL_CRED_<HOST>_TOKEN` env var (or `credentials={"token": "…"}`) is forwarded to it as storage options.
<!-- doctest: skip -->

    ```python
    pkg = Package.from_source(
        "https://data.example.org/networks/leavenworth/link.parquet",
        credentials={"token": "ey...JWT..."},
    )
    ```

??? note "Azure Blob Storage (`az://`)"
    Requires `pip install 'dbcorral[azure]'` (brings in `adlfs`). The `az://`, `abfs://` and `abfss://` schemes are all routed to it; credentials follow the same cascade.
<!-- doctest: skip -->

    ```python
    pkg = Package.from_source("az://container/networks/leavenworth/")
    ```

??? note "Google Cloud Storage (`gs://`)"
    Requires `pip install 'dbcorral[gcs]'` (brings in `gcsfs`). Set `GOOGLE_APPLICATION_CREDENTIALS` to the path of your service-account JSON file.
<!-- doctest: skip -->

    ```python
    pkg = Package.from_source("gs://my-bucket/networks/leavenworth/")
    ```

??? note "Force anonymous reads on a public bucket"
    The AWS chain still attempts to sign requests if any credential is present in the environment. Force unsigned access:
<!-- doctest: skip -->

    ```python
    pkg = Package.from_source(
        "s3://public-open-data/networks/leavenworth/",
        credentials={"anon": True},
    )
    ```

## Pitfalls

* **Credential precedence is fixed and not configurable.** A kwarg always overrides env / keyring / netrc. If your CI keeps reading the wrong credential, check whether something upstream is passing `credentials=` to `from_source`.
* **A remote `.duckdb` file is not supported.** DuckDB databases are read from local paths only — download it first (or convert the source to parquet / CSV, which do read over URLs).
* **Pre-signed URLs have TTLs.** If you pass an `https://…?X-Amz-Signature=…` URL, the credential is baked in and the URL expires. Re-issue for long-running jobs.
* **Listing a bucket isn't free.** `Package.from_source("s3://bucket/")` (no prefix) walks the bucket. Always include the package directory prefix.
* **Anonymous reads need an explicit signal.** For public buckets the AWS chain still attempts to sign requests if any credential is present in the environment. Pass `credentials={"anon": True}` to force unsigned access.
* **Keyring on Linux needs a backend.** corral only trusts the real OS keyring backends (macOS Keychain, Windows Credential Locker, SecretService / libsecret / KWallet); file-based `keyrings.alt` stores are deliberately ignored. On a headless box use the env-var path instead.

## See also

* [Architecture](../architecture.md) — package/engine layering and the `from_source` dispatcher.
* [Convert formats](convert-formats.md) — once loaded, write the package out as parquet or duckdb for faster repeat loads.
* [API reference](../reference/api.md) — `corral.read`, `Package.from_source`.
