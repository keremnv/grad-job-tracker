# Owner-approved backup alerts only. No compute, network, database or IAM role change.
# Existing resources are reconciled through normal S3-backed imports, NOT a root
# apply while the protected EC2 user-data replacement remains unresolved.
resource "aws_sns_topic" "backup_alerts" {
  name = "grad-jobwatch-backup-alerts"
}
resource "aws_sns_topic_policy" "backup_alerts" {
  arn = aws_sns_topic.backup_alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowReviewedCloudWatchAlarms"
      Effect    = "Allow"
      Principal = { Service = "cloudwatch.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.backup_alerts.arn
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.expected_project_id }
        ArnEquals = { "aws:SourceArn" = [
          aws_cloudwatch_metric_alarm.host.arn,
          aws_cloudwatch_metric_alarm.credits.arn,
          aws_cloudwatch_metric_alarm.failures.arn,
          aws_cloudwatch_metric_alarm.backup_health.arn
        ] }
      }
    }]
  })
}
resource "aws_sns_topic_subscription" "backup_email" {
  count     = var.backup_notification_email == null ? 0 : 1
  topic_arn = aws_sns_topic.backup_alerts.arn
  protocol  = "email"
  endpoint  = var.backup_notification_email
}
resource "aws_cloudwatch_log_metric_filter" "backup_health" {
  name           = "jobwatch-backup-health"
  log_group_name = aws_cloudwatch_log_group.operations.name
  pattern        = "{ $.operation = \"backup_health\" }"
  metric_transformation {
    name      = "BackupHealthy"
    namespace = "JobWatch"
    value     = "$.backup_healthy"
    unit      = "Count"
    # No default zero: unrelated collector records are not backup observations.
  }
}
resource "aws_cloudwatch_metric_alarm" "backup_health" {
  alarm_name                = "grad-jobwatch-backup-health"
  namespace                 = "JobWatch"
  metric_name               = "BackupHealthy"
  statistic                 = "Minimum"
  period                    = 900
  evaluation_periods        = 3
  datapoints_to_alarm       = 2
  comparison_operator       = "LessThanThreshold"
  threshold                 = 1
  treat_missing_data        = "breaching"
  alarm_actions             = [aws_sns_topic.backup_alerts.arn]
  ok_actions                = [aws_sns_topic.backup_alerts.arn]
  insufficient_data_actions = [aws_sns_topic.backup_alerts.arn]
}
