# Offline provider mocks. These tests never create AWS resources.
mock_provider "aws" {
  mock_data "aws_vpc" { defaults = { id = "vpc-test" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-test"] } }
  mock_data "aws_subnet" { defaults = { id = "subnet-test", map_public_ip_on_launch = true } }
  mock_data "aws_route_tables" { defaults = { ids = ["rtb-test"] } }
  mock_data "aws_route_table" {
    defaults = { routes = [{ cidr_block = "0.0.0.0/0", gateway_id = "igw-test" }] }
  }
  mock_data "aws_ami" { defaults = { id = "ami-00000000000000000" } }
}
variables {
  expected_project_id = "000000000000"
  ami_id              = "ami-00000000000000000"
}
run "safe_initial_plan" {
  command = plan
  # Computed ARNs must be known for the policy assertions; never apply the mock.
  override_resource {
    target          = aws_sns_topic.backup_alerts
    values          = { arn = "arn:aws:sns:eu-north-1:000000000000:fixture-alerts" }
    override_during = plan
  }
  override_resource {
    target          = aws_cloudwatch_metric_alarm.host
    values          = { arn = "arn:aws:cloudwatch:eu-north-1:000000000000:alarm:fixture-host" }
    override_during = plan
  }
  override_resource {
    target          = aws_cloudwatch_metric_alarm.credits
    values          = { arn = "arn:aws:cloudwatch:eu-north-1:000000000000:alarm:fixture-credits" }
    override_during = plan
  }
  override_resource {
    target          = aws_cloudwatch_metric_alarm.failures
    values          = { arn = "arn:aws:cloudwatch:eu-north-1:000000000000:alarm:fixture-failures" }
    override_during = plan
  }
  override_resource {
    target          = aws_cloudwatch_metric_alarm.backup_health
    values          = { arn = "arn:aws:cloudwatch:eu-north-1:000000000000:alarm:fixture-backup_health" }
    override_during = plan
  }
  assert {
    condition     = length(aws_security_group.tracker.ingress) == 0
    error_message = "Inbound ports configured."
  }
  assert {
    condition     = aws_ebs_volume.database.encrypted && aws_ebs_volume.database.final_snapshot && aws_instance.tracker.root_block_device[0].encrypted
    error_message = "Encrypted durable storage required."
  }
  assert {
    condition = alltrue([for r in aws_s3_bucket_lifecycle_configuration.backups.rule :
      alltrue([for e in r.expiration : e.days == 30]) &&
      alltrue([for e in r.noncurrent_version_expiration : e.noncurrent_days == 30])
    ])
    error_message = "Keep the existing 30-day current/noncurrent retention; shortening is not approved."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.backup_health.period == 900 &&
      aws_cloudwatch_metric_alarm.backup_health.datapoints_to_alarm == 2 &&
      aws_cloudwatch_metric_alarm.backup_health.treat_missing_data == "breaching" &&
      length(aws_cloudwatch_metric_alarm.backup_health.alarm_actions) == 1 &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[0].Principal.Service == "cloudwatch.amazonaws.com" &&
      length(jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[0].Condition.ArnEquals["aws:SourceArn"]) == 4 &&
      length(jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement) == 2 &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[1].Sid == "AllowReviewedCalendarAlarm" &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[1].Principal.Service == "cloudwatch.amazonaws.com" &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[1].Action == "sns:Publish" &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[1].Resource == "arn:aws:sns:eu-north-1:000000000000:fixture-alerts" &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[1].Condition.StringEquals["aws:SourceAccount"] == "000000000000" &&
      jsondecode(aws_sns_topic_policy.backup_alerts.policy).Statement[1].Condition.ArnEquals["aws:SourceArn"] == "arn:aws:cloudwatch:eu-north-1:000000000000:alarm:grad-jobwatch-calendar-health"
    )
    error_message = "Backup alerts need missing-heartbeat handling and a scoped CloudWatch-only publisher."
  }
  assert {
    condition     = aws_instance.tracker.metadata_options[0].http_tokens == "required" && aws_instance.tracker.credit_specification[0].cpu_credits == "standard"
    error_message = "IMDSv2 and bounded CPU charges required."
  }
}
# Same five mock runs; no live bucket update or notification is performed.
run "reject_wrong_region" {
  command = plan
  variables { region = "us-east-1" }
  expect_failures = [var.region]
}
run "reject_unreviewed_size" {
  command = plan
  variables { instance_type = "m8g.large" }
  expect_failures = [var.instance_type]
}
run "reject_wrong_architecture_image" {
  command = plan
  variables { ami_id = "latest" }
  expect_failures = [var.ami_id]
}
run "runtime_role_has_no_backup_or_secret_reads" {
  command = plan
  override_resource {
    target          = aws_s3_bucket.backups
    values          = { arn = "arn:aws:s3:::fixture-backups" }
    override_during = plan
  }
  override_resource {
    target          = aws_cloudwatch_log_stream.operations
    values          = { arn = "arn:aws:logs:eu-north-1:000000000000:log-group:/grad-jobwatch/operations:log-stream:operations" }
    override_during = plan
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.runtime.policy).Statement[1].Action == "s3:GetObject" &&
      jsondecode(aws_iam_role_policy.runtime.policy).Statement[1].Resource == "arn:aws:s3:::fixture-backups/artifacts/*" &&
      jsondecode(aws_iam_role_policy.runtime.policy).Statement[2].Action == "s3:PutObject" &&
      length(jsondecode(aws_iam_role_policy.runtime.policy).Statement) == 4
    )
    error_message = "Runtime role must not read backups, secrets or private owner state."
  }
}
