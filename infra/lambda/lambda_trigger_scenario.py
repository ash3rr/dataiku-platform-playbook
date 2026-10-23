"""Lambda handler: start a DSS scenario when objects land in S3.

Wire an S3 event notification (ObjectCreated) on the landing bucket to this
function. Each invocation starts one scenario run and passes the new object
keys as run parameters, which the scenario can read from its trigger params.

Environment variables:

    DSS_HOST          https://dss.example.com (reachable from the Lambda's VPC)
    DSS_SECRET_ARN    Secrets Manager secret holding {"api_key": "..."}
    PROJECT_KEY       DSS project key
    SCENARIO_ID       scenario to run

The API key should belong to a user or be a project key limited to running
scenarios in this one project.

Uses only the standard library and boto3, both available in the Lambda
Python runtime.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import boto3

_api_key = None


def api_key():
    global _api_key
    if _api_key is None:
        secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["DSS_SECRET_ARN"])
        _api_key = json.loads(secret["SecretString"])["api_key"]
    return _api_key


def new_objects(event):
    for record in event.get("Records", []):
        if not record.get("eventName", "").startswith("ObjectCreated"):
            continue
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        if key.endswith("/"):  # folder placeholder objects
            continue
        yield record["s3"]["bucket"]["name"], key


def run_scenario(params):
    host = os.environ["DSS_HOST"].rstrip("/")
    project = urllib.parse.quote(os.environ["PROJECT_KEY"])
    scenario = urllib.parse.quote(os.environ["SCENARIO_ID"])
    url = f"{host}/public/api/projects/{project}/scenarios/{scenario}/run"

    # DSS API keys use HTTP basic auth with the key as the user and no password.
    auth = base64.b64encode(f"{api_key()}:".encode()).decode()
    req = urllib.request.Request(
        url,
        data=json.dumps(params).encode(),
        method="POST",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or b"{}")


def handler(event, context):
    objects = [{"bucket": b, "key": k} for b, k in new_objects(event)]
    if not objects:
        return {"started": False, "reason": "no new objects"}

    try:
        result = run_scenario({"objects": objects})
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")[:500]
        # Raising makes Lambda retry the async invocation. A scenario that is
        # already running may reject the request; see docs/platform/event-driven-scenario-runs.md.
        raise RuntimeError(f"DSS returned {err.code}: {body}") from err

    print(json.dumps({"objects": len(objects), "run": result}))
    return {"started": True, "objects": len(objects)}
