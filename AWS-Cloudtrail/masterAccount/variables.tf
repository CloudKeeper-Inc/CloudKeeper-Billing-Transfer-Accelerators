variable "admin_account" {
  
}

variable "member_account_ids" {
  
}

variable "provider_region" {
  
}

variable "trail_name" {
  type = string
  default = "aws-multi-account-cloudtrail-logs"
}

variable "kms" {
  type = bool
  default = false
}

variable "cloudwatchLogs" {
  type = bool
  default = false
}

variable "sns" {
  type = bool
  default = false
}
