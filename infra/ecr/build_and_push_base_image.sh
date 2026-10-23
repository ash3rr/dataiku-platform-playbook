#!/usr/bin/env bash
# Build the DSS container-exec base image after an upgrade and push it to ECR.
#
# Run on the DSS design node as the dataiku user, from the DSS data directory
# (e.g. /data/dataiku/dss_data). Do not run it as ssm-user: in an SSM session
# ssm-user has no AWS credentials ("NoCredentials: Unable to locate
# credentials", which also makes docker login fail with "password is empty")
# and no access to the docker socket ("permission denied while trying to
# connect to the docker API at unix:///var/run/docker.sock"). Switch first:
#     sudo su - dataiku
#
# History, so the reasons for each step are not lost:
#
# 1. After the 14.5.1 upgrade this image was built on the EC2 host and
#    recorded as "pushed". It was never pushed. Containers on the GPU
#    profile failed with
#      <REGISTRY>/dataiku-containerexec-base-cuda:dss-14.5.1: not found
#    and DSS kept using the 14.2.1 base. Building only puts the image in the
#    local docker store, and EKS pulls from ECR.
#
# 2. When we pushed it ourselves, every layer sat on "Preparing" then
#    "Waiting" forever, including a tiny busybox test image. The vendor called
#    it a network issue. It was not: IAM, VPC endpoints, private DNS, security
#    groups, the S3 gateway route and disk space were all fine, and a manual
#    blob upload with curl returned 202 Accepted. The cause was that building
#    the base image made DSS start rebuilding and pushing every code studio
#    template image at once (one concurrent docker push per template, dockerd
#    at 98% CPU). Our push queued behind them. Once the count reached 0 the push
#    finished instantly, every layer "already exists".
#
# 3. The instance role (<DSS_INSTANCE_ROLE>) can push (InitiateLayerUpload,
#    UploadLayerPart, CompleteLayerUpload, BatchCheckLayerAvailability,
#    PutImage) but has no ecr:ListImages. "aws ecr list-images" from the host
#    returns AccessDenied. That is expected and does not block a push.
#
# Repeat for every environment (dev, UAT, prod) after each one is upgraded.
# Each environment's ECR needs its own copy of the new base.
set -euo pipefail

DSS_VERSION="${DSS_VERSION:-14.5.1}"
AWS_REGION="${AWS_REGION:-eu-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"
REPO="dataiku-containerexec-base-cuda"
TAG="dss-${DSS_VERSION}"
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

if [ "$(id -un)" != "dataiku" ]; then
  echo "Run this as the dataiku user (sudo su - dataiku)." >&2
  exit 1
fi

if [ ! -x ./bin/dssadmin ]; then
  echo "Run this from the DSS data directory (./bin/dssadmin not found)." >&2
  exit 1
fi

# 1. Build. Python 3.10, 3.11 and 3.12, no R, CUDA 11.8. This is the exact
#    command used for 14.5.1. It takes a while and produces ~6.3 GB.
./bin/dssadmin build-base-image --type container-exec \
  --with-py310 --with-py311 --with-py312 --without-r \
  --with-cuda --cuda-version=11.8 \
  --tag "${REPO}:${TAG}"

docker images | grep "${REPO}" || true

# 2. Repo exists and the ECR API answers. (The repo was created with
#    MUTABLE tags, scanOnPush false, AES256 encryption.)
aws ecr describe-repositories --repository-names "${REPO}" --region "${AWS_REGION}"

# 3. Wait for DSS's own code studio image builds and pushes to drain. They
#    start on their own after the base image build and saturate dockerd.
#    Manual check: ps aux | grep "docker push" | grep -v grep | wc -l
while [ "$(pgrep -fc 'docker push' || true)" -gt 0 ]; do
  echo "$(date -u +%H:%M:%S) waiting for $(pgrep -fc 'docker push') docker push process(es) started by DSS..."
  sleep 30
done

# 4. Tag, log in, push. The ECR token lasts 12 hours.
docker tag "${REPO}:${TAG}" "${REGISTRY}/${REPO}:${TAG}"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"
docker push "${REGISTRY}/${REPO}:${TAG}"

# 5. Confirm it landed. The instance role may not be allowed to describe
#    images. If this fails, run the same command from a workstation with an
#    admin profile:
#      aws ecr describe-images --repository-name dataiku-containerexec-base-cuda \
#        --image-ids imageTag=dss-14.5.1 --region eu-west-1 --profile prod
aws ecr describe-images --repository-name "${REPO}" \
  --image-ids imageTag="${TAG}" --region "${AWS_REGION}" \
  || echo "describe-images failed from this host; verify from a workstation."

cat <<EOF
Done.
Next:
  - Re-run the containerized execution test in DSS (Administration > Settings >
    Containerized execution). The profiles should already reference
    ${REPO}:${TAG}; the original error was already asking for that tag.
  - Rebuild code env images (see docs/runbooks/code-env-container-images.md).
  - Keep the previous version's local images (dss-14.2.1 and its pyenv-*
    images) until the new base is confirmed working everywhere.
EOF
