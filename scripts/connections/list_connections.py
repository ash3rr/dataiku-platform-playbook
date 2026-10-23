"""List every DSS connection with its host and how it authenticates.

Prints one line per connection by default. Secrets are never printed: any
parameter whose name looks like a password, secret, token or key is shown as
"<redacted>".

    python list_connections.py
    python list_connections.py --type EC2 --csv connections.csv
    python list_connections.py --name my-s3-template --json   # full params, redacted

Needs an API key with admin rights. Inside a DSS notebook, import main() and
call it, or run the file with %run.
"""

import argparse
import csv
import json
import re
import sys

from dss_client import get_client

SECRET = re.compile(r"pass|secret|token|privatekey|private_key|apikey|api_key|credential(?!smode)", re.I)

# Parameter names that say where a connection points. Names differ between
# connection types and DSS versions, so this is a best-effort list.
LOCATION_KEYS = [
    "host", "account", "url", "connectionURL", "endpoint", "region",
    "db", "database", "warehouse", "role", "bucket", "chbucket",
    "defaultManagedBucket", "defaultManagedPath", "metastoreSynchronizationMode",
]

# Parameter names that say how a connection authenticates.
AUTH_KEYS = [
    "authType", "credentialsMode", "useOAuth", "useInstanceRole",
    "assumeRoleARN", "stsRoleToAssume", "roleToAssume", "user",
    "usePerUserCredentials", "perUserCredentialsMode",
]


def redact(value, key=""):
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if key and SECRET.search(key) and value not in (None, "", False):
        return "<redacted>"
    return value


def load_connections(client):
    """Return {name: definition}. list_connections() returns full definitions
    on the versions this was used with; fall back to one call per connection."""
    conns = client.list_connections()
    if isinstance(conns, dict) and all(isinstance(v, dict) for v in conns.values()):
        return conns
    names = client.list_connections_names("all")
    return {n: client.get_connection(n).get_definition() for n in names}


def summarise(name, definition):
    params = definition.get("params", {}) or {}
    row = {
        "name": name,
        "type": definition.get("type", ""),
        "usable_by": definition.get("usableBy", ""),
        "allowed_groups": ",".join(definition.get("allowedGroups", []) or []),
    }
    for key in LOCATION_KEYS + AUTH_KEYS:
        if params.get(key) not in (None, ""):
            row[key] = redact(params[key], key)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--type", help="only this connection type, e.g. Snowflake, EC2 (S3), PostgreSQL")
    parser.add_argument("--name", help="only this connection")
    parser.add_argument("--json", action="store_true", help="print full redacted definitions as JSON")
    parser.add_argument("--csv", metavar="PATH", help="also write a CSV summary")
    args = parser.parse_args(argv)

    connections = load_connections(get_client())
    selected = {
        n: d for n, d in sorted(connections.items())
        if (not args.type or d.get("type", "").lower() == args.type.lower())
        and (not args.name or n == args.name)
    }

    if args.json:
        json.dump({n: redact(d) for n, d in selected.items()}, sys.stdout, indent=2, default=str)
        print()
        return

    rows = [summarise(n, d) for n, d in selected.items()]
    print(f"{len(rows)} connections")
    for row in rows:
        extras = "  ".join(f"{k}={v}" for k, v in row.items() if k not in ("name", "type"))
        print(f"{row['type']:<16} {row['name']:<40} {extras}")

    if args.csv:
        fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("name", "type"), k))
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
