# Runtime operations named explicitly; no app SDK-policy inference or human role assignment.
resource "aws_iam_role" "tracker" {
  name_prefix = "grad-jobwatch-instance-"
  path        = "/grad-jobwatch/" # never edit the project's /managed/ human-access roles
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" }
  }] })
}
resource "aws_iam_instance_profile" "tracker" {
  name_prefix = "grad-jobwatch-"
  path        = "/grad-jobwatch/"
  role        = aws_iam_role.tracker.name
}
resource "aws_iam_role_policy" "runtime" {
  name = "jobwatch-runtime"
  role = aws_iam_role.tracker.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Sid       = "SessionManagerOnly", Effect = "Allow", Resource = "*",
      Action    = ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"],
      Condition = { StringEquals = { "aws:RequestedRegion" = var.region } }
    },
    {
      Sid      = "ReadReleaseCodeOnly", Effect = "Allow", Action = "s3:GetObject",
      Resource = "${aws_s3_bucket.backups.arn}/artifacts/*"
    },
    {
      Sid       = "WriteVersionedBackups", Effect = "Allow", Action = "s3:PutObject",
      Resource  = "${aws_s3_bucket.backups.arn}/backups/*",
      Condition = { StringEquals = { "s3:x-amz-server-side-encryption" = "AES256" } }
    },
    {
      Sid      = "PublicOperationalSummaryOnly", Effect = "Allow", Action = "logs:PutLogEvents",
      Resource = aws_cloudwatch_log_stream.operations.arn
    }
  ] })
}
# In particular: no GetObject on backups, DeleteObject/Version, IAM, SendCommand,
# Parameter Store/Secrets Manager reads or schema/database credentials.
