"""Create a group's S3 connection by copying a template connection.

Set up one S3 connection by hand in the DSS admin UI with everything the way
you want it (STS with AssumeRole, managed path, readability settings, Snowflake
fast-write off or on). This script copies it, swaps in the group's bucket and
role, and restricts it to the group.

Parameter names vary by DSS version, so the fields to change are passed with
--param. Check your template first:

    python list_connections.py --name s3-template --json

then, for example:

    python clone_connection_for_group.py \\
        --template s3-template \\
        --group risk-analytics --env dev \\
        --param bucket=dss-dev-risk-analytics-123456789012 \\
        --param stsRoleToAssume=arn:aws:iam::123456789012:role/dss-grp-risk-analytics-dev \\
        --dry-run

Without --dry-run it creates the connection. It refuses to overwrite an
existing connection unless --replace is given.
"""

import argparse
import copy
import json
import sys

from dss_client import get_client


def parse_params(pairs):
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--param must be key=value, got {pair!r}")
        out[key] = value
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--template", required=True, help="name of the connection to copy")
    p.add_argument("--group", required=True, help="DSS group that may use the new connection")
    p.add_argument("--env", required=True, choices=["dev", "uat", "prod"])
    p.add_argument("--name", help="new connection name (default: s3-<env>-<group>)")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="connection parameter to set; repeat as needed")
    p.add_argument("--replace", action="store_true", help="overwrite the connection if it exists")
    p.add_argument("--dry-run", action="store_true", help="print the new definition and stop")
    args = p.parse_args(argv)

    name = args.name or f"s3-{args.env}-{args.group}"
    overrides = parse_params(args.param)
    client = get_client()

    template = client.get_connection(args.template).get_definition()
    missing = [k for k in overrides if k not in template.get("params", {})]
    if missing:
        print(f"warning: template has no params named {missing}; they will be added as new keys. "
              "Check the spelling against list_connections.py --json.", file=sys.stderr)

    definition = copy.deepcopy(template)
    definition["name"] = name
    definition.setdefault("params", {}).update(overrides)
    definition["usableBy"] = "ALLOWED"
    definition["allowedGroups"] = [args.group]

    if args.group not in [g["name"] for g in client.list_groups()]:
        raise SystemExit(f"DSS group {args.group!r} does not exist")

    if args.dry_run:
        print(json.dumps(definition, indent=2, default=str))
        return

    existing = set(client.list_connections_names("all"))
    if name in existing:
        if not args.replace:
            raise SystemExit(f"connection {name!r} exists; use --replace to overwrite it")
        conn = client.get_connection(name)
    else:
        conn = client.create_connection(
            name,
            type=template["type"],
            params=definition["params"],
            usable_by="ALLOWED",
            allowed_groups=[args.group],
        )

    # create_connection only takes the basics; set_definition carries over the
    # rest of the template (readability, managed-dataset settings and so on).
    current = conn.get_definition()
    for key, value in definition.items():
        if key not in ("name",):
            current[key] = value
    conn.set_definition(current)
    print(f"{'replaced' if name in existing else 'created'} {name} for group {args.group}")


if __name__ == "__main__":
    main()
