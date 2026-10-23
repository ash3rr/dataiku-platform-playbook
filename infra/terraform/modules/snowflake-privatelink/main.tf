resource "aws_security_group" "endpoint" {
  name        = "${var.name}-vpce"
  description = "Snowflake PrivateLink endpoint"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-vpce" })
}

# Snowflake needs 443 for the service and 80 for OCSP certificate checks.
# Keyed by position so the keys are known at plan time even when the source
# security groups are created in the same apply.
resource "aws_vpc_security_group_ingress_rule" "from_sources" {
  for_each = {
    for i, pair in setproduct(var.source_security_group_ids, [443, 80]) :
    tostring(i) => { sg = pair[0], port = pair[1] }
  }

  security_group_id            = aws_security_group.endpoint.id
  referenced_security_group_id = each.value.sg
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_endpoint" "snowflake" {
  vpc_id              = var.vpc_id
  service_name        = var.endpoint_service_name
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = false

  tags = merge(var.tags, { Name = var.name })
}

# Private DNS so the privatelink hostnames resolve to the endpoint from inside
# this VPC only.
resource "aws_route53_zone" "privatelink" {
  name    = "privatelink.snowflakecomputing.com"
  comment = "Snowflake PrivateLink for ${var.name}"

  vpc {
    vpc_id = var.vpc_id
  }

  tags = var.tags
}

resource "aws_route53_record" "snowflake" {
  for_each = toset(var.privatelink_hostnames)

  zone_id = aws_route53_zone.privatelink.zone_id
  name    = each.value
  type    = "CNAME"
  ttl     = 300
  records = [aws_vpc_endpoint.snowflake.dns_entry[0].dns_name]
}
