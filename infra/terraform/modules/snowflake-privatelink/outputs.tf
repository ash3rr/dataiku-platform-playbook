output "vpce_id" {
  description = "Put this in the AWSVPCEID network rule on the matching Snowflake account."
  value       = aws_vpc_endpoint.snowflake.id
}

output "security_group_id" {
  value = aws_security_group.endpoint.id
}
