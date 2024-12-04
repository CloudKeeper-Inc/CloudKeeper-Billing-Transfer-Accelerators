output "cloud_watch_logs_role_arn" {
  value = aws_iam_role.cloudtrail_logging_role[0].arn
}

output "cloud_watch_logs_group_arn" {
  value = aws_cloudwatch_log_group.logs[0].arn
}

output "sns_topic_name" {
  value = aws_sns_topic.test[0].arn
}
