output "instance_id" {
  value     = aws_instance.tracker.id
  sensitive = true
}
output "data_volume_id" {
  value     = aws_ebs_volume.database.id
  sensitive = true
}
output "backup_bucket" {
  value     = aws_s3_bucket.backups.id
  sensitive = true
}
output "log_group" { value = aws_cloudwatch_log_group.operations.name }
