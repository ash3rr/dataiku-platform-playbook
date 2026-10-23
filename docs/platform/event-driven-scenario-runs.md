# Starting scenarios when new data arrives

Three ways to run a DSS scenario when files land in S3, from least to most moving parts.

## 1. Scheduled scenario that checks for new files

A time-based trigger (every 15 minutes, hourly) starts a scenario whose first step lists the managed folder and stops early if there's nothing new. Or use a dataset-change trigger on a dataset built over the folder.

Nothing outside DSS to maintain. Latency is the trigger interval. For most batch work, including a computer vision pipeline fed a few times a day, this is enough. Start here.

## 2. S3 event, Lambda, DSS API

```
S3 ObjectCreated -> Lambda -> POST /public/api/projects/{key}/scenarios/{id}/run
```

The Lambda receives the event and calls the DSS public API to start the scenario, passing the new object keys as run parameters. [`scripts/lambda_trigger_scenario.py`](../../infra/lambda/lambda_trigger_scenario.py) does this.

Things to sort out:

- The Lambda needs a network path to DSS, so it runs in the DSS environment's VPC, and an API key from Secrets Manager.
- Give the key the narrowest rights that work: run scenarios in this one project.
- Uploads arrive one object at a time. A hundred images produce a hundred events. Starting a scenario that is already running may be rejected or queued depending on your version and settings, so for bursty input put an SQS queue between S3 and the Lambda and process in batches.

This is the S3 equivalent of what Snowpipe does for tables: event-driven, no polling.

## 3. Don't use the API node for this

The DSS API node serves real-time predictions: an outside application sends a request and gets a score back. It has nothing to do with reacting to new data. It only comes into the picture if the trained model is later exposed as an endpoint.
