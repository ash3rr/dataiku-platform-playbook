output "bucket_name" {
  value = aws_s3_bucket.this.id
}

output "bucket_arn" {
  value = aws_s3_bucket.this.arn
}

output "role_arn" {
  description = "Role ARN to put in the group's DSS S3 connection (STS with AssumeRole)."
  value       = aws_iam_role.group.arn
}
