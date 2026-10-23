# Getting files into Dataiku: S3 or a Snowflake stage?

Two questions came in together from a team starting a computer vision project: can DSS read a Snowflake stage through its Snowflake connection, and where should the images live?

## Snowflake stages through DSS

Yes, but only through SQL. A DSS Snowflake connection is a JDBC connection, and stages are reached with `PUT`, `GET`, `LIST` and `COPY INTO`, not with `SELECT`. So:

- a SQL recipe can run `COPY INTO my_table FROM @my_stage/path/` to load staged files into a table, which DSS then reads as an ordinary dataset;
- a Python recipe can open a Snowflake session with the connection's credentials and run `GET` to pull files down.

There is no way to browse a stage as a DSS managed folder.

## Where to put images

For binary files like images, a stage adds a hop without adding anything. Compare:

```
third-party tool -> S3 -> DSS managed folder
third-party tool -> S3 -> Snowflake stage -> back out again -> DSS
```

An external stage is itself backed by S3, and writing to an internal stage needs a Snowflake-aware client, which most SaaS tools aren't. Snowflake is the right destination for the *outputs* (labels, scores, predictions), not for the images.

The recommendation was a dedicated bucket: write access for the third party (a scoped IAM role, or pre-signed URLs if they can't assume a role), read access for DSS through a connection limited to the project's group, exposed in the project as a managed folder.

## What the team needed

The team's real aim was automation: images arrive, the pipeline runs, nobody presses a button. Storage was the easy part. The two things that actually needed answers were whether the tool can write to S3 natively (most SaaS tools can) and how DSS finds out new files have arrived. That's covered in [Starting scenarios when new data arrives](event-driven-scenario-runs.md).
