# DEV environment: the Design node's instance profile, per-group storage, and
# the Snowflake endpoint. UAT and PROD are the same with different inputs.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "dss_node_security_group_id" {
  type = string
}

variable "eks_worker_security_group_id" {
  type = string
}

variable "terraform_role_arn" {
  description = "Role this configuration runs as. Kept in every bucket policy."
  type        = string
}

variable "dss_groups" {
  description = "DSS groups that get storage in this environment. In practice this comes from the group registry, not a hand-edited list."
  type        = set(string)
  default     = ["group-a", "group-b"]
}

variable "snowflake_endpoint_service" {
  type = string
}

variable "snowflake_privatelink_hostnames" {
  type = list(string)
}

data "aws_caller_identity" "current" {}

locals {
  environment = "dev"
  account_id  = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------
# Node role: the trust anchor. No S3 access of its own; it can only assume
# this environment's group roles.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dss_node" {
  name               = "dss-node-${local.environment}"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

data "aws_iam_policy_document" "assume_group_roles" {
  statement {
    actions   = ["sts:AssumeRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/dss-grp-*-${local.environment}"]
  }
}

resource "aws_iam_role_policy" "assume_group_roles" {
  name   = "assume-group-roles"
  role   = aws_iam_role.dss_node.id
  policy = data.aws_iam_policy_document.assume_group_roles.json
}

resource "aws_iam_instance_profile" "dss_node" {
  name = aws_iam_role.dss_node.name
  role = aws_iam_role.dss_node.name
}

# ---------------------------------------------------------------------------
# One bucket and role per group
# ---------------------------------------------------------------------------

module "group_storage" {
  source   = "../../modules/dss-group-storage"
  for_each = var.dss_groups

  group_name            = each.key
  environment           = local.environment
  trust_anchor_role_arn = aws_iam_role.dss_node.arn
  admin_role_arns       = [var.terraform_role_arn]
  bucket_suffix         = "-${local.account_id}"
}

# ---------------------------------------------------------------------------
# Snowflake PrivateLink for the DEV/UAT account
# ---------------------------------------------------------------------------

module "snowflake_endpoint" {
  source = "../../modules/snowflake-privatelink"

  name                      = "snowflake-${local.environment}"
  vpc_id                    = var.vpc_id
  subnet_ids                = var.private_subnet_ids
  source_security_group_ids = [var.dss_node_security_group_id, var.eks_worker_security_group_id]
  endpoint_service_name     = var.snowflake_endpoint_service
  privatelink_hostnames     = var.snowflake_privatelink_hostnames
}

output "dss_node_instance_profile_arn" {
  description = "Goes in the Fleet Manager instance template for DEV nodes."
  value       = aws_iam_instance_profile.dss_node.arn
}

output "group_role_arns" {
  value = { for g, m in module.group_storage : g => m.role_arn }
}

output "snowflake_vpce_id" {
  value = module.snowflake_endpoint.vpce_id
}
