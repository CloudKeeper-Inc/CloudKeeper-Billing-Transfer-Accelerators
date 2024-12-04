# variable "admin_account" {
  
# }

variable "provider_region" {
  
}

variable "trail_name" {
  type = string
  default = "aws-multi-account-cloudtrail-logs"
}

variable "cloudwatchLogs" {
  type = bool
  default = false
}

variable "sns" {
  type = bool
  default = false
}
