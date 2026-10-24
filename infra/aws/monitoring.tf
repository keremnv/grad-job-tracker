resource "aws_cloudwatch_log_group" "operations" {
  name              = "/grad-jobwatch/operations"
  retention_in_days = 14
  # AWS-managed encryption at rest; public counts/status only, never owner records.
}
resource "aws_cloudwatch_log_stream" "operations" {
  name           = "operations"
  log_group_name = aws_cloudwatch_log_group.operations.name
}
resource "aws_cloudwatch_metric_alarm" "host" {
  alarm_name          = "grad-jobwatch-host-status"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = aws_instance.tracker.id }
  # Console alarms only: no email/contact or subscription is silently created.
}
resource "aws_cloudwatch_metric_alarm" "credits" {
  alarm_name          = "grad-jobwatch-cpu-credit-balance"
  namespace           = "AWS/EC2"
  metric_name         = "CPUCreditBalance"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 12
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = aws_instance.tracker.id }
}
resource "aws_cloudwatch_log_metric_filter" "failures" {
  name           = "jobwatch-operation-failures"
  log_group_name = aws_cloudwatch_log_group.operations.name
  pattern        = "{ $.exit_code > 0 }"
  metric_transformation {
    name      = "FailedOperations"
    namespace = "JobWatch"
    value     = "1"
  }
}
resource "aws_cloudwatch_metric_alarm" "failures" {
  alarm_name          = "grad-jobwatch-operation-failure"
  namespace           = "JobWatch"
  metric_name         = "FailedOperations"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
}
