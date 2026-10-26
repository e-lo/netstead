---
title: Read from S3 with credentials
audience: users
kind: howto
summary: Load a Frictionless data package from S3, GCS, Azure or HTTPS with the credential cascade — explicit dict, env var, keyring — and know exactly which reader your credentials reach.
---

# Read from S3 with credentials

## When to use this

Your data lives on cloud storage and you want the same lazy-load surface you get from a local directory — predicate pushdown, column pruning, no full download. Credentials should come from whichever channel your environment already manages (a CI secret, the OS keyring, a one-off dict in a notebook) without ever being baked into source.

## Quick example

Open a package from an S3 prefix. The `credentials=` dict is a set of [fsspec storage options](https://filesystem-spec.readthedocs.io/en/latest/features.html#configuration) for the URL's scheme — for S3 that is the `s3fs` vocabulary (`key`, `secret`, `token`, `anon`, `endpoint_url`, …).
<!-- doctest: skip -->

```python
from corral import read

pkg = read(
    "s3://my-bucket/networks/leavenworth/",
    credentials={"key": "AKIA…", "secret": "…"},
)
print(f"{len(pkg.tables)} tables, {pkg['link'].count()} links")
```

Expected (will vary by package):

```text
9 tables, 339 links
```

The same keyword exists on `Package.from_source` and on netstead's `Network.from_source`:
<!-- doctest: skip -->

```python
from netstead import Network

net = Network.from_source(
    "s3://my-bucket/networks/leavenworth/",
    credentials={"key": "AKIA…", "secret": "…"},
)
print(f"{net.spec_version}: {net.links.count()} links")
```

Either way the load is lazy — `pkg['link']` / `net.links` is a `Table` wrapping an ibis expression, not a materialized frame.

## How a URL is read

Two readers are in play, and which one you get depends only on whether credentials were resolved:

* **Credentials resolved (explicit dict, env var or keyring)** — corral builds an fsspec filesystem from that dict (`s3fs`, `gcsfs`, `adlfs`, or fsspec's HTTP filesystem) and registers it on the DuckDB connection behind the engine. The prefix listing *and* every table read go through that filesystem. One filesystem is registered per `(scheme, host, credentials)` and reused; two reads with different credentials for the same host never share one, so a lazy expression keeps answering with the credentials it was opened with.
* **No credentials anywhere** — the URL goes straight to DuckDB's own `httpfs` reader, which handles `http(s)://` and `s3://`. For S3 it reads the standard `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` and `AWS_DEFAULT_REGION` variables when the extension loads. It does not read `~/.aws/credentials`, an IAM role or `AWS_ENDPOINT_URL` on its own.

`gs://` and `az://` have no credential-less path: give them credentials (for a public GCS bucket, `{"token": "anon"}`).

## Step-by-step

### 1. Install the driver for your scheme

`fsspec` ships with corral. The per-cloud drivers are corral extras — netstead does not add them:

```bash
pip install 'dbcorral[s3]'      # s3fs  → s3://
pip install 'dbcorral[gcs]'     # gcsfs → gs://
pip install 'dbcorral[azure]'   # adlfs → az:// abfs://   (combine extras with commas)
```

`ImportError: Install s3fs to access S3` on a credentialed read means the extra is missing.

### 2. Provide credentials

Credentials resolve in a fixed order; the first layer that produces something wins, and the result is handed to fsspec as-is:

| Layer | Where | Resulting dict |
|---|---|---|
| 1. Explicit kwarg | `credentials={...}` | exactly what you passed |
| 2. Environment | `CORRAL_CRED_<HOST>_KEY` + `CORRAL_CRED_<HOST>_SECRET` | `{"key": …, "secret": …}` |
| 2. Environment | `CORRAL_CRED_<HOST>_TOKEN` | `{"token": …}` |
| 3. Keyring | service `corral`, username `<host>` | `{"token": <password>}` |
| 4. `~/.netrc` | machine `<host>` | `{"username": …, "password": …}` |

`<HOST>` is the URL host, upper-cased, with `.` and `-` flattened to `_`: for `s3://my-bucket/…` that is `MY_BUCKET`; for `az://container@account/…` it is the account; for HTTPS it is the hostname. For S3 use the `_KEY` + `_SECRET` pair (`token` alone is only a session token). For GCS, `_TOKEN` is the path to a service-account JSON file (or `anon`).

To store a token in the OS keyring once, on a dev machine:
<!-- doctest: skip -->

```python
import keyring
keyring.set_password("corral", "my-bucket", "/path/to/service-account.json")
```

To see which layer a host resolves from — never the value — ask the cascade:

```python
from corral.io.credentials import credential_source

print(credential_source("my-bucket"))  # 'env', 'keyring', 'netrc' or 'none'
```

### 3. Load the package

Discovery via the cascade is the common path; an explicit dict overrides it, which is handy in notebooks where you'd rather not depend on the shell environment:
<!-- doctest: skip -->

```python
from corral import read

# Discover via the cascade:
pkg = read("s3://my-bucket/networks/leavenworth/")

# Or pass the dict (always wins):
pkg = read(
    "s3://my-bucket/networks/leavenworth/",
    credentials={"key": "AKIA…", "secret": "…"},
)
```

A URL ending in `/` is listed: every child with a known extension (`.csv`, `.parquet`, `.csv.zip`, …) becomes a table; sidecars such as `_SUCCESS` are ignored. A URL to a single file loads that one table. The result is the same `Package` you'd get from a local path.

### 4. Verify the load is lazy

Each table wraps an ibis `Table`. `Table.filter` takes a callable over that expression and returns a new lazy table; the filter pushes down into the parquet reader, so only the survivors transit when you materialize (`to_polars()` needs the `polars` extra, `to_pandas()` the `pandas` one):
<!-- doctest: skip -->

```python
expr = pkg["link"].expr   # underlying ibis Table
print(type(expr).__name__)  # 'Table'

fast_links = pkg["link"].filter(lambda t: t.filter(t.free_speed > 45.0)).to_polars()
```

### 5. Cache for repeat reads

If the same script will hit the package many times — a notebook, a debugging loop, a CI matrix — write it locally once and load that afterwards:
<!-- doctest: skip -->

```python
import pathlib
from corral import Package

remote = Package.from_source("s3://my-bucket/networks/leavenworth/")
remote.write(pathlib.Path("cache") / "leavenworth.parquet")  # one-shot download; FileExistsError if it already exists

# Subsequent runs (no manifest is written — pass the spec again if you had one):
pkg = Package.from_source(pathlib.Path("cache") / "leavenworth.parquet")
```

`Package.write` / `Network.write` accept local destinations only; see [Convert formats](convert-formats.md) for the format trade-offs.

## Common variations

???+ note "S3 — AWS, MinIO, R2, Wasabi"
    Pass `s3fs` options. Non-AWS endpoints take `endpoint_url`; a public bucket takes `anon`.
<!-- doctest: skip -->

    ```python
    pkg = read(
        "s3://my-bucket/networks/leavenworth/",
        credentials={"key": "…", "secret": "…", "endpoint_url": "https://minio.local:9000"},
    )
    public = read("s3://public-open-data/networks/leavenworth/", credentials={"anon": True})
    ```

??? note "Google Cloud Storage (`gs://`)"
    `gcsfs` options: `token` is a service-account JSON path, `"anon"` for a public bucket, or `"google_default"` for the gcloud credentials on the machine. The same file path works as `CORRAL_CRED_MY_BUCKET_TOKEN`.
<!-- doctest: skip -->

    ```python
    pkg = read("gs://my-bucket/networks/leavenworth/", credentials={"token": "/path/to/sa.json"})
    ```

??? note "Azure Blob Storage (`az://`)"
    `adlfs` options: `account_name` plus one of `account_key`, `sas_token`, `connection_string` or `credential`; `anon=True` for a public container. The `az://`, `abfs://` and `abfss://` schemes all route to it. The env and keyring layers only produce a single `token`, which adlfs does not accept — use the explicit dict.
<!-- doctest: skip -->

    ```python
    pkg = read(
        "az://container@account/networks/leavenworth/",
        credentials={"account_name": "account", "account_key": "…"},
    )
    ```

??? note "HTTPS with a bearer token"
    The dict is passed to fsspec's HTTP filesystem as request options, so a header is the way to send a token. A `token` or `username`/`password` entry is *not* translated and fails at request time.
<!-- doctest: skip -->

    ```python
    pkg = read(
        "https://data.example.org/networks/leavenworth/link.parquet",
        credentials={"headers": {"Authorization": "Bearer ey…"}},
    )
    ```

??? note "Pre-signed or public HTTPS URL"
    With no credentials resolved the URL goes to DuckDB's `httpfs` reader directly. A pre-signed `https://…?X-Amz-Signature=…` URL carries its own credential and expires; re-issue it for long-running jobs.

## Pitfalls

* **Credential precedence is fixed.** A `credentials=` dict beats env, keyring and netrc. If CI keeps using the wrong credential, look for something upstream passing the kwarg.
* **The dict is not translated.** Keys must be the ones the scheme's fsspec driver accepts (`s3fs`, `gcsfs`, `adlfs`, aiohttp request options for HTTP). A wrong key surfaces as the driver's own error when the first byte is read, not at `read(...)`.
* **`.netrc` entries don't reach any driver as-is.** That layer yields `username`/`password`, which `s3fs` ignores and the HTTP filesystem rejects. Prefer the env variables or an explicit dict.
* **Credentials for a local path are an error.** The engine refuses `storage_options` for a non-URL source rather than silently ignoring them; `Package.from_source` never forwards `credentials=` to local reads.
* **A remote `.duckdb` file is not supported.** DuckDB databases are read from local paths only — download it first, or convert the source to parquet / CSV, which do read over URLs.
* **Keyring needs a real OS backend.** corral only trusts the macOS, Windows, SecretService, libsecret and KWallet backends; a headless box with `keyrings.alt` resolves to `none`. Use the env variables there.
* **Listing a bucket root isn't free.** `read("s3://bucket/")` with no prefix lists the whole bucket. Always include the package directory.
* **Non-AWS endpoints need the credentialed path.** For MinIO, R2, Wasabi and friends put `endpoint_url` in the credentials dict so the read goes through `s3fs`; DuckDB's credential-less reader does not pick up `AWS_ENDPOINT_URL`, and the cascade doesn't discover endpoints.
* **Writes to URLs go through the adapters, not `Package.write`.** `Package.write("s3://…")` raises `WriteUnsupportedForSchemeError`; write locally, then upload with your cloud tooling.

## See also

* [Architecture](../architecture.md) — package/engine layering and the `from_source` dispatcher.
* [Convert formats](convert-formats.md) — write the package out as parquet or duckdb for faster repeat loads.
* [API reference](../reference/api.md) — `corral.read`, `Package.from_source`, `corral.io.credentials`.
