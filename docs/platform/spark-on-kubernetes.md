# Spark on Kubernetes

DSS on this platform can submit Spark jobs to the EKS cluster. Two episodes are recorded here: a Spark job failure, and the discovery that Prepare recipes in a workshop project were running on Spark by default on small data.

## Recognising a Spark-on-Kubernetes job in the logs

* Log source `dku.spark.submit.k8s.helper`.
* JARs distributed to executor pods under paths like `/var/data/spark-<uuid>/` (`dataiku-dss-core.jar`, `dataiku-prepare-core.jar` and others).
* The resource usage line at the end of the job: `"type":"SPARK_K8S_JOB"` and, for a Prepare recipe, `"recipeType":"shaker"`.
* The driver runs on the DSS host (driver address was the design node's private IP). Executor logs alone do not name the cluster or node group, but the `kubectl` commands DSS logs at the end do: context `<INSTANCE_ID>@<EKS_CLUSTER_NAME>.eu-west-1.eksctl.io`.
* Per-user namespaces: `dss-spark-ns-<login with @ and . replaced by dashes>-<number>`. So DSS sanitises the login into a valid Kubernetes name on the Spark path. The same is not available for container execution profile names, where `${dssUserLogin}` is used raw (see [workshop-infrastructure.md](workshop-infrastructure.md)).
* Reads and writes against Snowflake go through staging, with a `COPY` into the target at the end.

## Visual recipes on Spark by default

### What we found

A Prepare recipe in a hands-on workshop project ran via `spark-submit` on EKS. The data was under 200k rows and under 100 MB uncompressed, read from and written to a Snowflake test database. The job took about 2 minutes 23 seconds, most of it overhead: executor pod scheduling, JAR distribution, Snowflake staging. The in-memory DSS engine or in-database SQL would do the same work in seconds.

Spark node group at the time:

| Setting | Value |
|---|---|
| AMI | Amazon Linux 2023 (x86_64) Standard, release `1.33.8-20260304` |
| Launch template | `eks-gp3-500gb` (gp3, 500 GB) |
| Instance type | r6i.2xlarge (8 vCPU, 64 GB, memory optimised) |
| Autoscaling | enabled |

Context: datasets in the workshop were all about the same size, and dozens of users worked in parallel. Every user's Prepare run requests executor pods on large memory nodes, so the autoscaler keeps adding r6i.2xlarge nodes to shuffle sub-100 MB datasets. Spark as the default engine for Prepare recipes was pre-existing configuration. We had not set it.

### Recommendation

Default visual recipes to the in-database (SQL) engine where the data is in Snowflake, or the DSS engine otherwise, and keep Spark for large data. The engine can be set per recipe or as a project default. Change it with whoever owns instance configuration rather than unilaterally, in case some project depends on it.

Questions raised and not answered: whether the platform admins wanted it changed, whether this was a training-only environment or also used for real work, and whether Spark had been chosen originally to work around Snowflake size or concurrency limits. Whether the default was changed is not recorded.

## Spark job failure

A PySpark recipe failed. The logs contained two independent problems.

### 1. The failure itself

```
NameError: name 'col' is not defined   (line 19)
```

The script used `col` from `pyspark.sql.functions` without importing it. DSS aborts at that line. Recipe owner fix:

```python
from pyspark.sql.functions import col
```

### 2. Executors dying on JAR permissions

```
Permission denied: /data/dataiku/jar/iceberg-api-1.6.1.jar
```

and, on a later run, the same for `bundle-2.26.25.jar`. The JARs are listed in `spark.jars`, live on the DSS host under `/data/dataiku/jar/`, and executors stream them from the driver. Executors run as a different user from the file owner, fall into "others", and need the read bit. Fix on the DSS host (not the EKS nodes):

```bash
aws ssm start-session --target <INSTANCE_ID> --region eu-west-1 --profile <profile>
ls -la /data/dataiku/jar/iceberg-*.jar
sudo chmod o+r /data/dataiku/jar/iceberg-*.jar
# or, if the dataiku user should own them:
sudo chown dataiku:dataiku /data/dataiku/jar/iceberg-*.jar
```

`o+r` adds read for "others" only, leaving owner and group bits alone.

### What the retry showed

The rerun succeeded (`exit=0`, `runActivity terminated, success=true`) and the data reached Snowflake (the `COPY` finished). Executors still failed on `bundle-2.26.25.jar`, but Spark kept requesting replacements until enough started. So the JAR problem is not fatal for every recipe, but it wastes time and pods on every run.

The logs also showed repeated:

```
Unable to create executor due to null
```

A Kubernetes rejection for a real reason (insufficient resources, node selector mismatch) gives a descriptive message. `null` is more consistent with executors dying right after start, which Spark reports like a scheduling failure. Most likely the JAR permission problem again, not an EKS fault. Test: fix the permissions and rerun. If the `null` errors stop, it was the JARs. Whether the `chmod` was applied for all JARs and the errors went away is not recorded.

## When Spark is the right tool

For transformations that cannot be expressed in SQL on very large data, Spark on EKS with the Snowflake Spark connector is a reasonable choice and DSS can orchestrate it. For anything that fits in SQL, keep it in Snowflake, and for anything small, use the DSS engine. The billion-row discussion in [eks-vs-snowflake-compute.md](eks-vs-snowflake-compute.md) mentions Spark as one option.
