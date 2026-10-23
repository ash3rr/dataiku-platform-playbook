variable "group_name" {
  description = "DSS group name, lowercased and hyphenated (e.g. risk-analytics)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,29}$", var.group_name))
    error_message = "group_name must be lowercase letters, digits and hyphens, at most 30 characters."
  }
}

variable "environment" {
  description = "Environment short name: dev, uat or prod."
  type        = string

  validation {
    condition     = contains(["dev", "uat", "prod"], var.environment)
    error_message = "environment must be dev, uat or prod."
  }
}

variable "trust_anchor_role_arn" {
  description = "ARN of the environment's DSS node role, the only principal allowed to assume the group role."
  type        = string
}

variable "admin_role_arns" {
  description = "Roles that keep full access to the bucket, at minimum the role Terraform runs as. Leaving this out locks Terraform out of the bucket."
  type        = list(string)

  validation {
    condition     = length(var.admin_role_arns) > 0
    error_message = "Pass at least the role Terraform runs as."
  }
}

variable "name_prefix" {
  description = "Prefix for bucket and role names."
  type        = string
  default     = "dss"
}

variable "bucket_suffix" {
  description = "Appended to the bucket name to make it globally unique, e.g. '-123456789012'."
  type        = string
  default     = ""
}

variable "kms_key_arn" {
  description = "KMS key for default bucket encryption. Null uses SSE-S3."
  type        = string
  default     = null
}

variable "archive_access_days" {
  description = "Days without access before objects move to the Archive Access tier. Null disables it. Archived objects must be restored before DSS can read them."
  type        = number
  default     = 90
}

variable "deep_archive_access_days" {
  description = "Days without access before objects move to the Deep Archive Access tier. Null disables it."
  type        = number
  default     = 180
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
