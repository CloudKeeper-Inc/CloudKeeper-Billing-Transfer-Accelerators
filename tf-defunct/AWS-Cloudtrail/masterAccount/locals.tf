locals {
  provider_profile = replace(terraform.workspace, format(".%s", var.provider_region), "")
}
