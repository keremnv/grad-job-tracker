data "aws_caller_identity" "current" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "selected" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "availability-zone"
    values = [var.availability_zone]
  }
}
data "aws_subnet" "selected" { id = sort(data.aws_subnets.selected.ids)[0] }
data "aws_route_tables" "explicit" {
  filter {
    name   = "association.subnet-id"
    values = [data.aws_subnet.selected.id]
  }
}
data "aws_route_tables" "main" {
  vpc_id = data.aws_vpc.default.id
  filter {
    name   = "association.main"
    values = ["true"]
  }
}
data "aws_route_table" "selected" {
  # Default subnets commonly inherit the main route table without an explicit association.
  route_table_id = length(data.aws_route_tables.explicit.ids) > 0 ? one(data.aws_route_tables.explicit.ids) : one(data.aws_route_tables.main.ids)
}
data "aws_ami" "ubuntu" {
  owners = ["099720109477"] # Canonical, not a project identifier
  filter {
    name   = "image-id"
    values = [var.ami_id]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
}
resource "aws_security_group" "tracker" {
  name_prefix = "grad-jobwatch-"
  description = "No inbound traffic; SSM and ATS use outbound HTTPS"
  vpc_id      = data.aws_vpc.default.id
  ingress     = []
  egress {
    description = "HTTPS plus HTTP package redirects; DNS uses the VPC resolver"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "ATS, SSM, S3, package repositories"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "grad-jobwatch-no-ingress" }
}
resource "aws_ebs_volume" "database" {
  availability_zone = var.availability_zone
  size              = var.data_gib
  type              = "gp3"
  encrypted         = true
  final_snapshot    = true
  tags              = { Name = "grad-jobwatch-postgresql", Data = "durable" }
  lifecycle { prevent_destroy = true }
}
resource "aws_instance" "tracker" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.selected.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.tracker.id]
  iam_instance_profile        = aws_iam_instance_profile.tracker.name
  monitoring                  = false
  disable_api_termination     = true
  user_data_base64            = base64gzip(local.cloud_init)
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 12
    encrypted             = true
    delete_on_termination = true # OS only: database is on the protected separate volume
  }
  tags = { Name = "grad-jobwatch" }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition = data.aws_subnet.selected.map_public_ip_on_launch && anytrue([
        for r in data.aws_route_table.selected.routes : r.cidr_block == "0.0.0.0/0" && startswith(coalesce(r.gateway_id, "none"), "igw-")
      ])
      error_message = "Selected subnet needs the reviewed direct Internet Gateway route; never silently add NAT."
    }
  }
}
resource "aws_volume_attachment" "database" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.database.id
  instance_id                    = aws_instance.tracker.id
  force_detach                   = false
  stop_instance_before_detaching = true
}
locals {
  cloud_init = join("\n", ["#cloud-config", jsonencode({
    write_files = [
      { path = "/opt/jobwatch-bootstrap/bootstrap_host.sh", permissions = "0700", content = file("${path.module}/scripts/bootstrap_host.sh") },
      { path = "/opt/jobwatch-bootstrap/deploy_release.py", permissions = "0700", content = file("${path.module}/scripts/deploy_release.py") },
      { path = "/opt/jobwatch-bootstrap/aws-cli-signing-key.asc", permissions = "0644", content = file("${path.module}/aws-cli-signing-key.asc") },
      { path = "/opt/jobwatch-bootstrap/bootstrap.sql", permissions = "0600", content = file("${path.module}/../../sql/postgresql/bootstrap.sql") },
      { path = "/etc/jobwatch/aws.json", permissions = "0644", content = jsonencode({
        region    = var.region, bucket = aws_s3_bucket.backups.id,
        log_group = aws_cloudwatch_log_group.operations.name, log_stream = aws_cloudwatch_log_stream.operations.name
      }) }
    ]
    runcmd = [["bash", "/opt/jobwatch-bootstrap/bootstrap_host.sh", aws_ebs_volume.database.id, "--initialize-empty-volume"]]
  })])
}
