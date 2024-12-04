terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "5.43.0"
    }
  }
}

provider "aws" {
  region = var.provider_region
  profile = local.provider_profile
}

provider "aws" {
  alias  = "s3"
  region = var.bucketRegion  
  profile = local.provider_profile
}

provider "aws" {
  alias  = "sns"
  region = var.snsRegion  
  profile = local.provider_profile
}
