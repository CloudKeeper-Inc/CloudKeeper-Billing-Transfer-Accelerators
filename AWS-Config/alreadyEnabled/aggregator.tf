resource "aws_config_aggregate_authorization" "example" {
  account_id = var.admin_account
  region     = var.aggregator_region
}
