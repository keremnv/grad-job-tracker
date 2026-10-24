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
  assert {
    condition     = length(aws_security_group.tracker.ingress) == 0
    error_message = "Inbound ports configured."
  }
  assert {
    condition     = aws_ebs_volume.database.encrypted && aws_ebs_volume.database.final_snapshot && aws_instance.tracker.root_block_device[0].encrypted
    error_message = "Encrypted durable storage required."
  }
  assert {
    condition     = aws_instance.tracker.metadata_options[0].http_tokens == "required" && aws_instance.tracker.credit_specification[0].cpu_credits == "standard"
    error_message = "IMDSv2 and bounded CPU charges required."
  }
}
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
