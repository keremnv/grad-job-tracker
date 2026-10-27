# PREPARED ONLY. Separate state key prevents existing bootstrap replacement drift.
terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 6.0" } }
  backend "s3" {
    key          = "integrations/terraform.tfstate"
    region       = "eu-north-1"
    profile      = "grad-jobwatch"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  profile             = "grad-jobwatch"
  region              = "eu-north-1"
  allowed_account_ids = [var.expected_project_id]
  default_tags { tags = { Project = "graduate-job-watch", Component = "agent-calendar", ManagedBy = "terraform" } }
}
provider "aws" {
  alias   = "global"
  profile = "grad-jobwatch"
  region  = "us-east-1" # CloudFront global API only
  # Regional resources stay Stockholm.
  allowed_account_ids = [var.expected_project_id]
}
variable "expected_project_id" {
  type      = string
  sensitive = true
}
variable "calendar_bucket_name" { type = string }
variable "instance_role_name" { type = string }
variable "alert_topic_arn" {
  type      = string
  sensitive = true
}
variable "capability_hashes" {
  type        = map(string)
  description = "One-way SHA-256 digests only, never URL tokens or private URLs."
  validation {
    condition     = length(var.capability_hashes) >= 1 && alltrue([for k, v in var.capability_hashes : contains(["deadlines", "actions", "combined"], k) && can(regex("^[0-9a-f]{64}$", v))])
    error_message = "Only named feeds with 64-character SHA-256 digests are allowed."
  }
}
resource "aws_s3_bucket" "calendar" {
  bucket        = var.calendar_bucket_name
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "calendar" {
  bucket = aws_s3_bucket.calendar.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "calendar" {
  bucket = aws_s3_bucket.calendar.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_ownership_controls" "calendar" {
  bucket = aws_s3_bucket.calendar.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_public_access_block" "calendar" {
  bucket                  = aws_s3_bucket.calendar.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_lifecycle_configuration" "calendar" {
  bucket     = aws_s3_bucket.calendar.id
  depends_on = [aws_s3_bucket_versioning.calendar]
  rule {
    id     = "derived-noncurrent-versions"
    status = "Enabled"
    filter { prefix = "" }
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_cloudfront_origin_access_control" "calendar" {
  provider                          = aws.global
  name                              = "jobwatch-calendar"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
resource "aws_cloudfront_function" "capability" {
  provider = aws.global
  name     = "jobwatch-calendar-capability"
  runtime  = "cloudfront-js-2.0"
  publish  = true
  code     = templatefile("${path.module}/calendar-capability.js.tftpl", { capability_hashes = var.capability_hashes })
}
resource "aws_cloudfront_cache_policy" "calendar" {
  provider    = aws.global
  name        = "jobwatch-calendar-short-cache"
  min_ttl     = 0
  default_ttl = 60
  max_ttl     = 60
  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}
data "aws_cloudfront_response_headers_policy" "security" {
  provider = aws.global
  name     = "Managed-SecurityHeadersPolicy"
}
resource "aws_cloudfront_distribution" "calendar" {
  provider        = aws.global
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Private capability ICS only - never the application backup bucket"
  price_class     = "PriceClass_100"
  origin {
    domain_name              = aws_s3_bucket.calendar.bucket_regional_domain_name
    origin_id                = "calendar-only"
    origin_access_control_id = aws_cloudfront_origin_access_control.calendar.id
  }
  default_cache_behavior {
    target_origin_id           = "calendar-only"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.calendar.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.capability.arn
    }
  }
  viewer_certificate { cloudfront_default_certificate = true }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  # URI access logging is deliberately OFF: it would record bearer capability URLs.
  # Use aggregate CloudFront metrics, publisher health and control-plane audit instead.
  custom_error_response {
    error_code            = 403
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    error_caching_min_ttl = 0
  }
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_policy" "calendar" {
  bucket     = aws_s3_bucket.calendar.id
  depends_on = [aws_s3_bucket_public_access_block.calendar]
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid    = "OnlyThisDistribution", Effect = "Allow", Principal = { Service = "cloudfront.amazonaws.com" },
      Action = "s3:GetObject", Resource = [for f in ["deadlines", "actions", "combined"] : "${aws_s3_bucket.calendar.arn}/${f}.ics"],
    Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.calendar.arn, "AWS:SourceAccount" = var.expected_project_id } } },
    { Sid = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.calendar.arn, "${aws_s3_bucket.calendar.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }
  ] })
}
resource "aws_iam_role_policy" "publish_calendar" {
  name = "jobwatch-publish-minimal-calendar"
  role = var.instance_role_name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "s3:PutObject",
    Resource = [for f in ["deadlines.ics", "actions.ics", "combined.ics", "_private/snapshot.json"] : "arn:aws:s3:::${var.calendar_bucket_name}/${f}"],
  Condition = { StringEquals = { "s3:x-amz-server-side-encryption" = "AES256" } } }] })
}
resource "aws_cloudwatch_log_metric_filter" "calendar_health" {
  name           = "jobwatch-calendar-health"
  log_group_name = "/grad-jobwatch/operations"
  pattern        = "{ $.operation = \"calendar\" }"
  metric_transformation {
    name      = "CalendarHealthy"
    namespace = "JobWatch"
    value     = "$.healthy"
    unit      = "Count"
  }
}
resource "aws_cloudwatch_metric_alarm" "calendar_health" {
  alarm_name          = "grad-jobwatch-calendar-health"
  namespace           = "JobWatch"
  metric_name         = "CalendarHealthy"
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alert_topic_arn]
  ok_actions          = [var.alert_topic_arn]
}
output "calendar_domain" { value = aws_cloudfront_distribution.calendar.domain_name }
output "calendar_bucket" {
  value     = aws_s3_bucket.calendar.id
  sensitive = true
}
