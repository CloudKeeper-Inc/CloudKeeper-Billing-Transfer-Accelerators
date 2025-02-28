# Multi-Account AWS Organization Services Replication

## Overview
This project automates the replication of an AWS Organizations services into a multi-account AWS setup. It ensures that logs from member accounts are centrally collected in the master account, maintaining compliance and visibility across the AWS organization.

## Prerequisites

Before you begin the migration process, ensure you have the following prerequisites in place:

1. **AWS CLI**:
   - Install AWS CLI if not already installed. Follow the [AWS CLI installation guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html).
   - Configure the AWS CLI with your AWS credentials.

2. **Configuring AWS CLI**:
   - Ensure that you have AWS secret keys and access keys inside the credentials file with the profile name as the account number (including root management account). The default profile should have access keys of your organization's root account. Your `~/.aws/credentials` file should look like this:
     ```
     #root management account
     [default]  
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
    
     #root management account
     [123456789123]  
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
     
     [789456123789]
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
     
     [456123789456]
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
     ```

## Installation
1. Clone the repository:
   ```sh
   git clone https://github.com/CloudKeeper-Inc/CloudKeeper-Billing-Transfer-Accelerators.git
   ```
2. Install dependencies:
   ```sh
   pip install boto3
   ```

## Troubleshooting
- **Profile Not Found Error:** Ensure the AWS profile is correctly set up:
  ```sh
  aws configure list-profiles
  ```
- **Permission Denied Errors:** Verify IAM role permissions for SecurityHub, S3, IAM, Cloudtrail, GuardDuty, KMS, Config.

## License
MIT License

## Author
[**Jatin Rautela**](https://github.com/JatinTTN)
