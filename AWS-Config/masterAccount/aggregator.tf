resource "aws_config_configuration_aggregator" "account" {
  name = "multi-account-config"

  account_aggregation_source {
    account_ids = concat([var.admin_account], var.member_account_ids)
    regions     = var.regions
  }
}
