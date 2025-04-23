output "cloud_watch_logs_role_arn" {
  value = {for k, v in aws_iam_role.cloudtrail_logging_role: k => v.arn}
}

output "cloud_watch_logs_group_arn" {
  value = {for k, v in aws_cloudwatch_log_group.logs: k => v.arn}
}

output "sns_topic_name" {
  value = {for k, v in aws_sns_topic.test: k => v.arn}
}
