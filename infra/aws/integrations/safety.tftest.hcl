mock_provider "aws" {}
mock_provider "aws" { alias = "global" }
variables {
  expected_project_id  = "000000000000"
  calendar_bucket_name = "jobwatch-synthetic-calendar-test"
  instance_role_name   = "synthetic-existing-role"
  alert_topic_arn      = "arn:aws:sns:eu-north-1:000000000000:synthetic"
  capability_hashes    = { deadlines = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", actions = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
}
run "private_calendar_without_host_or_backup_changes" {
  command = plan
  assert {
    condition     = aws_s3_bucket.calendar.force_destroy == false && aws_s3_bucket_public_access_block.calendar.block_public_policy
    error_message = "Calendar origin must stay private and protected."
  }
  assert {
    condition     = aws_cloudfront_origin_access_control.calendar.signing_behavior == "always" && aws_cloudfront_distribution.calendar.default_cache_behavior[0].viewer_protocol_policy == "https-only"
    error_message = "Signed private origin and viewer HTTPS are required."
  }
  assert {
    condition     = aws_cloudfront_cache_policy.calendar.min_ttl == 0 && aws_cloudfront_cache_policy.calendar.max_ttl == 60
    error_message = "Refresh/revocation must not rely on a long cache lifetime."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.publish_calendar.policy).Statement[0].Resource) == 4 && jsondecode(aws_iam_role_policy.publish_calendar.policy).Statement[0].Action == "s3:PutObject"
    error_message = "Publisher writes only the three feeds and private transport metadata."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.calendar_health.treat_missing_data == "breaching"
    error_message = "Missing publication heartbeat must alert."
  }
}
