variable "name" {
  description = "Name for the endpoint and its security group, e.g. snowflake-prod."
  type        = string
}

variable "vpc_id" {
  description = "VPC to create the endpoint in. One endpoint per environment VPC."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the endpoint's network interfaces, one per AZ."
  type        = list(string)
}

variable "source_security_group_ids" {
  description = "Security groups allowed to reach the endpoint: DSS nodes and EKS workers."
  type        = list(string)
}

variable "endpoint_service_name" {
  description = "The privatelink-vpce-id value from SYSTEM$GET_PRIVATELINK_CONFIG()."
  type        = string
}

variable "privatelink_hostnames" {
  description = <<-EOT
    Every *.privatelink.snowflakecomputing.com hostname returned by
    SYSTEM$GET_PRIVATELINK_CONFIG(): the account URL, the regionless account URL,
    the OCSP URL and the Snowsight URLs.
  EOT
  type        = list(string)

  validation {
    condition     = alltrue([for h in var.privatelink_hostnames : endswith(h, ".privatelink.snowflakecomputing.com")])
    error_message = "All hostnames must end in .privatelink.snowflakecomputing.com."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
