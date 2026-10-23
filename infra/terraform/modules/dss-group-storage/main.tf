locals {
  bucket_name = "${var.name_prefix}-${var.environment}-${var.group_name}${var.bucket_suffix}"
  role_name   = "${var.name_prefix}-grp-${var.group_name}-${var.environment}"

  tags = merge(var.tags, {
    "dss:group"       = var.group_name
    "dss:environment" = var.environment
  })
}

# ---------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "this" {
  bucket = local.bucket_name
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == null ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = var.kms_key_arn != null
  }
}

# Intelligent-Tiering decides by last access, not by age. Objects have to be in
# the INTELLIGENT_TIERING storage class for that, so move them on day 0.
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "to-intelligent-tiering"
    status = "Enabled"
    filter {}

    transition {
      days          = 0
      storage_class = "INTELLIGENT_TIERING"
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_intelligent_tiering_configuration" "archive" {
  count = var.archive_access_days == null && var.deep_archive_access_days == null ? 0 : 1

  bucket = aws_s3_bucket.this.id
  name   = "archive-unused"
  status = "Enabled"

  dynamic "tiering" {
    for_each = var.archive_access_days == null ? [] : [var.archive_access_days]
    content {
      access_tier = "ARCHIVE_ACCESS"
      days        = tiering.value
    }
  }

  dynamic "tiering" {
    for_each = var.deep_archive_access_days == null ? [] : [var.deep_archive_access_days]
    content {
      access_tier = "DEEP_ARCHIVE_ACCESS"
      days        = tiering.value
    }
  }
}

# ---------------------------------------------------------------------------
# Group role, assumed by the environment's DSS node role through the
# group's S3 connection
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [var.trust_anchor_role_arn]
    }
  }
}

resource "aws_iam_role" "group" {
  name                 = local.role_name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = local.tags
}

data "aws_iam_policy_document" "group_access" {
  statement {
    sid       = "ListOwnBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.this.arn]
  }

  statement {
    sid = "ReadWriteOwnObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
      "s3:RestoreObject",
    ]
    resources = ["${aws_s3_bucket.this.arn}/*"]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [var.kms_key_arn]
    content {
      sid       = "UseBucketKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "group_access" {
  name   = "s3-${local.bucket_name}"
  role   = aws_iam_role.group.id
  policy = data.aws_iam_policy_document.group_access.json
}

# ---------------------------------------------------------------------------
# Bucket policy: deny everyone except the group role and the admin roles,
# so a broad IAM policy elsewhere can't open the bucket. The account root
# user can still delete this policy if it ever locks everyone out.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "bucket" {
  statement {
    sid       = "DenyNonTls"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyAllButGroupAndAdmins"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    # aws:PrincipalArn is the role ARN even for assumed-role sessions.
    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values   = concat([aws_iam_role.group.arn], var.admin_role_arns)
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.bucket.json

  depends_on = [aws_s3_bucket_public_access_block.this]
}
