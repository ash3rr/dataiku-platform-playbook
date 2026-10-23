# Temporary infrastructure for simulating ML model production behaviour

A ticket asked for an EKS container with at least 64 cores and 150 GB of RAM for temporary use, to simulate ML model production behaviour before deployment. CPU only. A related request asked about raising the partition concurrency limit of 7 (see [compute-and-cost.md](../architecture/compute-and-cost.md#partition-build-concurrency)). The two are linked because more concurrent activities need more compute.

## Questions that shape it

* Duration: hours, days, or weeks. Decides on-demand versus spot versus reserved.
* GPU or CPU. If production serves a model on GPU, simulating on CPU with 150 GB RAM behaves differently and may not tell you what you need. Here: CPU.
* How close to production: the full serving stack (model server, autoscaling, ingress, monitoring) or raw compute to load the model and observe it.
* Data access: S3, a database, an external endpoint, latency sensitivity.
* Single workload or splittable across nodes. Here: assumed one instance.

## Instance choice (eu-west-1, approximate on-demand prices at the time)

| Instance | vCPU | RAM | $/hr | Fit |
|---|---|---|---|---|
| m7i.16xlarge | 64 | 256 GB | ~3.80 | Chosen: covers 150 GB with headroom |
| r7i.16xlarge | 64 | 512 GB | ~4.80 | More memory than needed |
| c7i.16xlarge | 64 | 128 GB | ~3.20 | Cheapest but below 150 GB |

Spot for the m7i.16xlarge was estimated at about $1.50 to $1.60/hr (AWS can reclaim it with two minutes' notice, so only if the simulation can checkpoint and restart). The EKS control plane is about $0.10/hr, so about $3.90/hr all-in on demand.

By duration: for a few hours to a couple of days, run on demand and tear down. For a week or two, consider spot. At a month or more a savings plan starts to make sense, which is probably overkill for "temporary".

## Design

* One m7i.16xlarge node in its own node group, autoscaling to zero when no workload is scheduled, so only the control plane costs money while idle (about $2.40/day).
* Karpenter suggested over Cluster Autoscaler: provisions faster (typically 30 to 60 seconds against minutes) and a `NodePool` can target m7i.16xlarge with spot preferred and on-demand as fallback. Whether Karpenter was adopted is not recorded. The cluster otherwise used Cluster Autoscaler.
* Cold start from zero for a node this size: expect 2 to 3 minutes before the pod runs. Fine for a run you start and leave, annoying when iterating.
* The pod must request close to the full size (around 64 CPU and 150 GB), otherwise the autoscaler brings up a smaller node.
* If production does not run on Kubernetes, a plain EC2 instance with Docker, started and stopped by hand or on a schedule (for example a Lambda), avoids the $73/month control plane and the node group plumbing. EKS is worth it only if the simulation needs to mirror a Kubernetes production stack.

## Monthly cost

Fixed: EKS control plane about $73/month (0.10 x 730 hours). Variable: node hours. Recomputed from the same hourly rates (the original table had arithmetic errors in four of the node-hour cells, for example $681 for 160 hours at $3.80):

| Usage | Node hours | On demand ($3.80/hr) | Spot (~$1.55/hr) |
|---|---|---|---|
| 8 h/day, 20 workdays | 160 | ~$608 + $73 = ~$681 | ~$248 + $73 = ~$321 |
| 4 h/day, 20 workdays | 80 | ~$304 + $73 = ~$377 | ~$124 + $73 = ~$197 |
| 24/7, no scale to zero | 730 | ~$2,774 + $73 = ~$2,847 | ~$1,132 + $73 = ~$1,205 |

Plus small items: EBS for the node (roughly $10 to $20/month depending on size), data transfer out (usually minor), CloudWatch logs if enabled.

## Open

* Expected hours per day, which decides spot versus on-demand.
* Whether the request was fulfilled as an EKS node group or a plain EC2 instance.
* The Karpenter `NodePool` and sample pod spec were proposed but never written.
