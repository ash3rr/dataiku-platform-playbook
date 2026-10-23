# Heavy processing on EKS instead of Snowflake compute

A team ran a very heavy Python workload in DSS containers on EKS. It read batches of work units from a Snowflake table, processed them, and wrote results to an output table (Snowflake or a DSS-managed dataset). Spreading it across several pods made it slower: workers stepped on each other on both reads and writes and I/O crawled.

Scale, as described: thousands of units, each with possibly billions of rows. The team had already raised node memory to 128 GB, 500 GB of disk per node, and up to 12 nodes (about 1.5 TB RAM in total). They kept the processing out of Snowflake deliberately: warehouse time was the expensive part.

Nothing below was built or tested by us. It is the design we worked out and the reasons for it. The code is a design sketch.

## Why parallel was slower than serial

* Read races. No coordination, so several pods could pick the same batch.
* Write contention. Many pods inserting into the same table at once queue on locks. Small inserts also create many tiny Snowflake micro-partitions.
* Memory. A 128 GB pod cannot hold one unit of billions of rows, and anything that materialises a unit in pandas fails or swaps.
* Network. Every row goes Snowflake to pod to Snowflake, serialised and deserialised twice.
* Coordination overhead. With 12 pods competing, the cost of avoiding collisions can exceed the gain. You end up building a distributed database layer on top of a distributed database.

## Options, in the order we discussed them

### 1. Push the work into Snowflake

If the transformations can be written as SQL (window functions and CTEs included), keep data in the warehouse and let pods only orchestrate which units to process:

```sql
INSERT INTO <OUTPUT_TABLE>
SELECT item_id, <transformations>
FROM <INPUT_TABLE>
WHERE item_id IN (SELECT item_id FROM <QUEUE_TABLE> WHERE worker_id = '<worker>')
  AND <filters>;
```

In DSS: SQL recipes or visual recipes on the in-database engine, separate warehouses per stage, auto-suspend. If a recipe's execution shows "Local stream", data is being pulled through the pod. For Python-only logic: Snowpark (Python inside Snowflake) or external functions.

Rejected by the team for cost: this is the warehouse time they wanted to avoid.

### 2. Native Dataiku partitioning by unit

Partition the input and output datasets by unit id (discrete dimension), map input to output partitions, build partitions in parallel with concurrency set to the node count, and let DSS distribute the work. Inside the recipe the partition is available as `DKU_DST_<dimension>`.

Already tried by the team: they hit DSS's maximum number of partitions, and grouping units into bigger partitions made each partition too big.

Could the limit be raised? It may be a configuration matter that support can advise on, but we never found or verified a real property name. Property names that came up (`datasets.partitions.max_listed`, `datasets.partitions.max_computation`) are unverified guesses. Do not apply them without Dataiku confirming. Reasons not to push partition counts into the tens of thousands even if possible: dataset and partition-selector pages become very slow, partition metadata grows, job planning time grows, and a scheduler with 12 slots still queues the rest. Partitioning is designed for time-based or moderate-cardinality business dimensions, not as a work queue for high-cardinality ids.

A middle path: super-partitions. Map units into a manageable number of buckets (for example `NTILE(100) OVER (ORDER BY item_id)`), partition by bucket id, and process the units in a bucket sequentially. Keeps partition count under the limit and keeps DSS-native parallelism.

```sql
CREATE TABLE <MAPPING_TABLE> AS
SELECT item_id, NTILE(100) OVER (ORDER BY item_id) AS bucket_id
FROM (SELECT DISTINCT item_id FROM <INPUT_TABLE>);
```

The partition concurrency limits that apply to this option are described in [compute-and-cost.md](../architecture/compute-and-cost.md#partition-build-concurrency).

### 3. Work queue, stream through pods, bulk load (the design we recommended)

Shape:

1. A cheap query builds a manifest of unit ids (seconds of warehouse time).
2. Units go into a queue. Each worker claims a few units atomically, so no two workers ever read the same unit.
3. The worker streams each unit from Snowflake in chunks (5 to 10 million rows), transforms, and writes compressed Parquet to its own S3 prefix. No shared write target, no locks.
4. When workers finish, one `COPY INTO` loads all the Parquet into the output table. Snowflake builds proper micro-partitions, and bulk load is far faster than row or batch INSERTs.
5. Small warehouses: X-Small for reads and queue updates, a larger one only for the final load.

Queue options considered:

| Queue | For | Against |
|---|---|---|
| Small Snowflake table | One system, easy monitoring with SQL | Each claim is a DML statement that resumes a warehouse (60-second minimum billing) and serialises on table locks. No `RETURNING` in Snowflake, and primary keys are not enforced |
| Redis | Microsecond claims, no warehouse cost | Another service to run on or next to the cluster |
| DynamoDB or SQS | Managed, AWS-native | Another service and IAM to set up |

Target file size for `COPY INTO` was given as 100 to 250 MB per Parquet file.

#### Rough cost comparison (estimates)

These are estimates made while designing, not measurements. Snowflake credit price depends on edition and region, so recheck before quoting.

| | Parallel INSERT (as is) | Stream plus bulk load |
|---|---|---|
| Snowflake | Large warehouse about 4 hours, ~$80 to $320 | X-Small reads ~15 min (~$2 to $4), Medium `COPY INTO` ~5 min (~$2 to $4) |
| EKS | ~$100/day | ~$100/day |
| S3 staging | none | ~$5 to $20 for TB-scale data |
| Queue | none | Redis ~$10 to $50/month |
| Per run | ~$180 to $420 | ~$110 to $130 |

The 12-node cluster was estimated at $500 to $2,000/month against $10k+ of Snowflake compute for the same work. Also unverified.

### 4. Spark on EKS

For complex non-SQL transformations at this scale, DSS can orchestrate Spark on EKS with the Snowflake Spark connector. Mentioned, not pursued.

## DSS settings that were suggested, and their status

A long list of "settings to optimise" came up while designing. Several are real, several are not things DSS has. Recorded here so nobody goes looking for the wrong ones.

Real and worth checking:
* Recipe execution engine and container config (Kubernetes, not local) and whether the code env image is built (see [compute-and-cost.md](../architecture/compute-and-cost.md#silent-fallback-to-local)).
* Per-recipe container resource requests and limits. Keep requests close to limits to avoid evictions. On 128 GB nodes leave headroom for the system (100 GB request and 120 GB limit were used as the example).
* Chunked reads in Python instead of `get_dataframe()`:

  ```python
  for chunk in dataiku.Dataset("<input>").iter_dataframes(chunksize=5_000_000):
      ...
  ```

  and a single `get_writer()` kept open while writing chunks, rather than repeated `write_with_schema`.
* Read only needed columns (`columns=[...]` on `iter_dataframes`).
* Partition concurrency (global, per project or user, per recipe).
* Snowflake session parameters on the connection, for example `QUERY_TAG` and `STATEMENT_TIMEOUT_IN_SECONDS`, and separate warehouses for read-heavy and write-heavy work.
* Snowflake side: `CLUSTER BY (item_id)` on the input for pruning, transient tables for intermediate results (no Time Travel overhead), `QUERY_HISTORY` to look at queueing (`queued_overload_time`) and partitions scanned.
* Pod side: `kubectl top pods` to see whether pods are CPU or memory bound or just waiting on I/O.

Not confirmed or not real, do not go looking for them: a dataset option for "COPY INTO write method" with S3 staging and file size, "uncheck create indexes", "skip integrity checks", "enable streamable mode", recipe logging options like "log query plans", connection pool sizes, and a scenario API like `scenario.run_recipe(..., variables=...)` in a loop. DSS containerized execution also has no "replicas" setting: each recipe run is one pod, so a Kubernetes `Deployment` with `replicas: 12` is not something you configure in a DSS container config.

## Open: how to run N workers from DSS

The design needs N concurrent workers. How to launch them inside DSS was not settled. Possibilities to evaluate:
* N copies of the worker recipe, each with its own (possibly dummy) output, built in parallel from one scenario step
* the super-partition variant, where DSS's own partition parallelism provides the N workers
* run the workers as a plain Kubernetes Job with `parallelism: N` outside DSS, using the same image, and keep DSS for orchestration and the final load.

Also open: how the pods get AWS credentials for S3 (node instance role or a pod-level role), how they get Snowflake credentials without hard-coding them, and the shape of the transformation (row-wise, within-unit windows, or cross-unit), which decides chunking.

## Design sketch: Snowflake queue worker

Never run. The version discussed had bugs that are fixed here. Snowflake has no `UPDATE ... RETURNING`, so claims use a token and a follow-up `SELECT`. `PRIMARY KEY` is not enforced, so the manifest load must avoid duplicates itself. Parquet writes need an explicit S3 filesystem. The bulk load goes through an external stage rather than a raw `s3://` path with keys. Credentials are deliberately left as a stub.

Queue table and manifest:

```sql
CREATE TABLE IF NOT EXISTS <QUEUE_TABLE> (
    item_id        VARCHAR,
    status         VARCHAR,        -- pending | processing | complete | failed
    worker_id      VARCHAR,
    claim_token    VARCHAR,
    start_time     TIMESTAMP_NTZ,
    end_time       TIMESTAMP_NTZ,
    attempt_count  INT DEFAULT 0,
    error_message  VARCHAR
);

INSERT INTO <QUEUE_TABLE> (item_id, status)
SELECT DISTINCT item_id, 'pending'
FROM <INPUT_TABLE>
WHERE item_id NOT IN (SELECT item_id FROM <QUEUE_TABLE>);
```

Worker (one per pod):

```python
# DESIGN SKETCH - not run. Placeholders in <...>.
import os
import socket
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import s3fs
import snowflake.connector

WORKER_ID = os.environ.get("HOSTNAME", socket.gethostname())  # pod name in K8s
BATCH_SIZE = 5                 # units claimed per round trip
CHUNK_SIZE = 5_000_000         # rows per fetch
S3_PREFIX = "s3://<BUCKET>/staging"
QUEUE = "<QUEUE_TABLE>"
SOURCE = "<INPUT_TABLE>"


def get_connection():
    # Stub: obtain credentials from a secret store or key-pair auth.
    # Never hard-code them in the recipe.
    return snowflake.connector.connect(
        account="<ACCOUNT>",
        user="<SERVICE_USER>",
        authenticator="SNOWFLAKE_JWT",
        private_key_file="<PATH_TO_MOUNTED_KEY>",
        warehouse="<XSMALL_WAREHOUSE>",
        database="<DATABASE>",
        schema="<SCHEMA>",
    )


def claim(conn, n):
    """Claim up to n pending units. Concurrent UPDATEs on one table should
    serialise on Snowflake's DML locks, and the status re-check stops two
    workers taking the same row. Verify under load before relying on it."""
    token = f"{WORKER_ID}-{uuid.uuid4()}"
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE {QUEUE}
               SET status = 'processing',
                   worker_id = %s,
                   claim_token = %s,
                   start_time = CURRENT_TIMESTAMP(),
                   attempt_count = attempt_count + 1
             WHERE status = 'pending'
               AND item_id IN (SELECT item_id FROM {QUEUE}
                               WHERE status = 'pending'
                               ORDER BY item_id LIMIT {int(n)})
            """,
            (WORKER_ID, token),
        )
        cur.execute(f"SELECT item_id FROM {QUEUE} WHERE claim_token = %s", (token,))
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()


def finish(conn, item_id, status, error=None):
    cur = conn.cursor()
    try:
        cur.execute(
            f"""UPDATE {QUEUE}
                   SET status = %s, end_time = CURRENT_TIMESTAMP(), error_message = %s
                 WHERE item_id = %s""",
            (status, error, item_id),
        )
    finally:
        cur.close()


def transform(df):
    # The real logic goes here.
    return df


def process(conn, fs, item_id):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {SOURCE} WHERE item_id = %s", (item_id,))
    columns = [d[0] for d in cur.description]
    chunk_no, total = 0, 0
    while True:
        rows = cur.fetchmany(CHUNK_SIZE)
        if not rows:
            break
        out = transform(pd.DataFrame(rows, columns=columns))
        path = f"{S3_PREFIX}/item_id={item_id}/{WORKER_ID}_chunk_{chunk_no:06d}.parquet"
        pq.write_table(pa.Table.from_pandas(out), path, filesystem=fs, compression="snappy")
        chunk_no += 1
        total += len(rows)
        del rows, out
    cur.close()
    return chunk_no, total


def main():
    conn = get_connection()
    fs = s3fs.S3FileSystem()   # credentials from the pod's AWS role
    done = 0
    while True:
        items = claim(conn, BATCH_SIZE)
        if not items:
            break
        for item_id in items:
            try:
                chunks, rows = process(conn, fs, item_id)
                finish(conn, item_id, "complete")
                done += 1
                print(f"[{WORKER_ID}] {item_id}: {rows:,} rows, {chunks} files")
            except Exception as exc:  # keep going; failed units can be re-queued
                finish(conn, item_id, "failed", str(exc)[:1000])
                print(f"[{WORKER_ID}] {item_id}: FAILED {exc}")
    conn.close()
    print(f"[{WORKER_ID}] finished, {done} unit(s)")


main()
```

Final load, once, after all workers finish (larger warehouse, external stage with a storage integration):

```sql
COPY INTO <OUTPUT_TABLE>
FROM @<EXTERNAL_STAGE>/staging/
FILE_FORMAT = (TYPE = PARQUET)
MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
PATTERN = '.*[.]parquet'
ON_ERROR = CONTINUE;
```

Monitoring and recovery:

```sql
SELECT status, COUNT(*) AS units, COUNT(DISTINCT worker_id) AS workers
FROM <QUEUE_TABLE> GROUP BY status;

SELECT worker_id, COUNT(*) AS done, AVG(DATEDIFF(second, start_time, end_time)) AS avg_s
FROM <QUEUE_TABLE> WHERE status = 'complete' GROUP BY worker_id;

-- stuck claims (pod died): put them back
UPDATE <QUEUE_TABLE>
   SET status = 'pending', claim_token = NULL
 WHERE status = 'processing'
   AND start_time < DATEADD(hour, -2, CURRENT_TIMESTAMP());
```

If the queue moves to Redis, claim with a single atomic command (for example `LMOVE pending processing RIGHT LEFT`, Redis 6.2 or later) rather than reading the list and then removing items, which races between workers. The Redis snippet in the first draft read with `LRANGE` and removed with `LREM` in a pipeline, which is not atomic across workers.

## What the team did

Not recorded. The design was handed over. Whether they implemented the queue, the super-partitions, or something else is unknown.
