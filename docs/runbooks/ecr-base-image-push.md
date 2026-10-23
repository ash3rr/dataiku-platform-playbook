# Container base image missing from ECR, then the push that hung

Two linked incidents after the prod upgrade to 14.5.1. The script that does the final, correct sequence is `infra/ecr/build_and_push_base_image.sh`.

## Incident 1: image built but never pushed

### Symptom

Containerized jobs on the GPU profile failed. Pod events showed:

```
<AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com/dataiku-containerexec-base-cuda:dss-14.5.1: not found
```

and jobs kept running on the 14.2.1 base.

### What had happened

The upgrade record said the image had been recreated on the EC2 host with:

```bash
./bin/dssadmin build-base-image --type container-exec --with-py310 --with-py311 --with-py312 --without-r --with-cuda --cuda-version=11.8 --tag dataiku-containerexec-base-cuda:dss-14.5.1
```

run from `dss_data` on the design node, and that the base images had then been pushed. There was no evidence of a push. `docker images | grep dataiku-containerexec-base-cuda` on the host showed `dss-14.5.1` present locally, alongside base and `pyenv-*` images going back through 13.0.2, 13.2.2, 13.4.1, 13.5.5 and 14.2.1 (some 14.2.1 `pyenv-*` images had been rebuilt recently, so they were in use). The image existed only in the local docker store. Last time (14.2.1) someone had built and then pushed.

### Fix

Tag, log in, push, verify. See the script. Before pushing, confirm the repo exists and credentials work:

```bash
aws ecr describe-repositories --repository-names dataiku-containerexec-base-cuda --region eu-west-1
aws ecr describe-images --repository-name dataiku-containerexec-base-cuda --image-ids imageTag=dss-14.5.1
```

The same build and push must be done in each environment (dev, UAT, prod) after its upgrade, because each has its own ECR.

### After the push

* The DSS containerized execution config was already asking for `dss-14.5.1` (that is what the error said), so no config change was needed.
* Re-run the container test in DSS.
* Code env images need rebuilding on the new base. DSS may do some of this lazily, but we rebuilt explicitly (`code-env-container-images.md`).

### Old local images

A large amount of disk on the host was taken by old images. Plan agreed, not executed at the time:

1. Do nothing until 14.5.1 is pushed and confirmed working in EKS and code envs are rebuilt.
2. Keep 14.2.1 base and `pyenv-*` images as a rollback.
3. The local copies are dead weight once the images are in ECR, because EKS pulls from ECR.
4. Prune the 13.x images first:

   ```bash
   docker images | grep "base-cuda:13\|base-cuda-13" | awk '{print $3}' | xargs docker rmi
   # or age based
   docker image prune -a --filter "until=720h"
   ```

   Check disk first (`df -h`). Docker's root on this host is `/data/docker-data`, which shares the DSS data volume.

## Incident 2: docker push hangs on "Waiting"

### Symptom

Pushing the base image from the design node sat with every layer on `Preparing` then `Waiting`, indefinitely. Dataiku support said it was a network issue between the host and ECR.

### What we ruled out, in order

| Check | Command | Result |
|---|---|---|
| Image present | `docker images \| grep dataiku-containerexec-base-cuda` | `13.5.5` 5.45 GB, `dss-14.2.1` 5.79 GB (in use), `dss-14.5.1` 6.34 GB |
| ECR API reachable | `aws ecr describe-repositories --region eu-west-1 --repository-name dataiku-containerexec-base-cuda` | OK. MUTABLE tags, scanOnPush false, AES256 |
| Login | `aws ecr get-login-password --region eu-west-1 \| docker login --username AWS --password-stdin <REGISTRY>` | `Login Succeeded` (warning that credentials are stored unencrypted in `/data/dataiku/.docker/config.json`) |
| IAM | `aws ecr list-images ...` | AccessDenied for `ecr:ListImages`. Looked like the cause, but was not. |
| IAM, properly | from the laptop: `aws iam list-attached-role-policies --role-name <DSS_INSTANCE_ROLE> --profile prod`, then `aws iam get-policy` / `get-policy-version` on the DataIku policy | Role has `S3_readwrite`, `dataiku-iam-roles-prod-DataIkuPolicy-<SUFFIX>`, `AmazonSSMManagedInstanceCore`, `AmazonEC2FullAccess`, `AWSCloudFormationFullAccess`. The DataIku policy grants `ecr:InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `BatchCheckLayerAvailability`, `PutImage` on `*`. No `ListImages`. Push permissions fine. (IAM reads from the instance itself are denied: `iam:ListAttachedRolePolicies`, `iam:GetRolePolicy`.) |
| BatchCheck reachable | `aws ecr batch-check-layer-availability --repository-name ... --layer-digests sha256:test` | `InvalidLayerDigest` failure, i.e. allowed and answering |
| Registry endpoint | `curl -s https://<REGISTRY>/v2/ -o /dev/null -w "%{http_code}"` | 401, which is correct without a token |
| Disk | `df -h /var/lib/docker` | No such directory. `docker info \| grep "Docker Root Dir"` gives `/data/docker-data`, with plenty of free space |
| Fresh token and retry | login then push immediately | Same hang |
| VPC endpoints | `aws ec2 describe-vpc-endpoints --filters "Name=service-name,Values=*ecr*"` and `*s3*` | `ecr.api` and `ecr.dkr` interface endpoints available, S3 gateway endpoint available |
| Endpoint subnets | describe the dkr endpoint vs the instance | Endpoints in three subnets, the instance in a fourth. Looked like the cause. But `PrivateDnsEnabled` is true, so DNS resolves to the endpoint from anywhere in the VPC |
| Security groups | describe endpoint SGs and instance SGs | Endpoint SG allows 443 from anywhere in the VPC. The instance's FM default SG allows all outbound |
| DNS and TLS | `curl -v https://<REGISTRY>/v2/` | Connects to a private 10.x endpoint address, TLS handshake fine |
| S3 route | `aws ec2 describe-route-tables --filters "Name=association.subnet-id,Values=<SUBNET>"` | S3 prefix list route to the gateway endpoint present. Also NAT for 0.0.0.0/0 and the transit gateway for 10.0.0.0/8 |
| Size related? | push `busybox` retagged as `:test-push` | Also hangs. Not size |
| Authenticated registry call | `TOKEN=$(aws ecr get-login-password --region eu-west-1); curl -s -u "AWS:$TOKEN" https://<REGISTRY>/v2/dataiku-containerexec-base-cuda/tags/list` | `DENIED ... ecr:ListImages`, so auth works. (Building the Basic header by hand with base64 gave `400 Bad Request`, so use `-u`.) |
| Blob upload start | `curl -v -u "AWS:$TOKEN" -X POST "https://<REGISTRY>/v2/dataiku-containerexec-base-cuda/blobs/uploads/"` | `202 Accepted` with a `Location` header and `OCI-Chunk-Min-Length: 10485760`. ECR accepts uploads |
| Docker version | `docker version` | 29.4.1 (API 1.54), containerd v2.2.3, runc 1.3.5. Current |
| Proxy | `/etc/systemd/system/docker.service.d/*.conf`, `~/.docker/config.json`, `docker info \| grep -iE "proxy\|registry\|mirror"` | No proxy, no mirror. Storage driver overlay2 |
| Daemon log | `sudo journalctl -u docker --since "30 minutes ago"` | `"Not continuing with push after error" error="context canceled"` (that was our Ctrl+C) and a flood of `sbJoin` messages |
| Swarm or busy host | `docker network ls`, `docker ps \| wc -l` | Three default networks, no running containers |

### Cause

The useful question came from our side: is anyone else using this host? `ps aux | grep -i docker | grep -v grep` showed DSS running a `docker build` and a `docker push` process for each code studio template, for example:

```
docker push <REGISTRY>/dku-kub-<template>:dss-14.5.1-build-<YYYY-MM-DD-HH-MM-SS-mmm>_1
docker build -t dku-kub-<template>:dss-14.5.1-build-... --force-rm /data/dataiku/dss_data/tmp/code-studio-build/<Template>/build-.../docker-build -t <REGISTRY>/dku-kub-<template>:...
/usr/libexec/docker/cli-plugins/docker-buildx buildx build ...
runc --log /data/docker-data/buildkit/executor/runc-log.json ...
```

dockerd was at 98.2% CPU.

```bash
ps aux | grep "docker push" | grep -v grep | wc -l     # one per code studio template
```

Building the new base image had made DSS rebuild every code studio template image on top of it and push them all at once. Our push, the busybox test and every DSS push were stuck behind a saturated daemon. The `sbJoin` flood was the build containers. We had not known that building the base triggers code studio rebuilds.

A few minutes later the count was 0. The base image push then completed at once:

```
$ docker push <REGISTRY>/dataiku-containerexec-base-cuda:dss-14.5.1
34ed94620765: Layer already exists
...
f291f81a8a17: Layer already exists
dss-14.5.1: digest: sha256:<DIGEST> size: 5782
```

Every layer already existed because the code studio images share the base layers and had pushed them.

The last attempts failed for a different reason first: in a fresh SSM session as ssm-user, `aws` had no credentials (`NoCredentials: Unable to locate credentials`, so `docker login` said `password is empty`) and docker refused the socket (`permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`). Everything here runs as `dataiku` (`sudo su - dataiku`).

### Takeaways

* "Network issue" from the vendor was wrong. Prove each layer (DNS, TLS, auth, upload start) before accepting it.
* Before pushing anything from the DSS host, check for concurrent docker activity. The script waits for `docker push` processes to drain.
* Expect the code studio rebuild storm whenever a base image is built. Plan it for a quiet period. See `code-studio-template-rebuild.md`.

## Open items

* Confirm the same build and push were done for the dev and UAT ECRs after their upgrades.
* Add `ecr:ListImages` and `ecr:DescribeImages` to the instance role so pushes can be verified from the host.
* Clean up the 13.x local images.
