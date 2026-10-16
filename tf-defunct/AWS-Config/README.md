# Multi-Account AWS Config Replication

## Overview
This project automates the replication of an AWS Organizations Config into a multi-account AWS Config setup. It ensures that Config logs from member accounts are centrally collected in the master account, maintaining compliance and visibility across the AWS organization.

## Features
- Detects if AWS Organizations Config is enabled.
- Replicates the organization-level Config setup in a multi-account structure.
- Configures AWS Config to pull logs from member accounts into the master account.

## Prerequisites

Before you begin the migration process, ensure you have the following prerequisites in place:

1. **Python and Terraform**:
   - Ensure that you have Python and Terraform installed and properly configured on your system.
   - [Install Python](https://www.python.org/downloads/)
   - [Install Terraform](https://learn.hashicorp.com/tutorials/terraform/install-cli)

2. **AWS CLI**:
   - Install AWS CLI if not already installed. Follow the [AWS CLI installation guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html).
   - Configure the AWS CLI with your AWS credentials.

3. **Configuring AWS CLI**:
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
   cd AWS-Config
   ```
2. Install dependencies:
   ```sh
   pip install boto3
   ```

## Usage
Run the script to initiate the multi-account Config setup:
```sh
python3 main.py
```

## How It Works
1. **Checks AWS Organizations Config:**
   - Fetches details of the existing organization Config.
2. **Replicates the Config Rules:**
   - Uses Terraform for creation of new resources.
   - Creates a new Config aggregator in the master account.
3. **Enables Cross-Account Logging:**
   - Updates member accounts to send logs to the master account.
   - Grants required permissions to the master account's IAM role.

## Troubleshooting
- **Profile Not Found Error:** Ensure the AWS profile is correctly set up:
  ```sh
  aws configure list-profiles
  ```
- **Permission Denied Errors:** Verify IAM role permissions for Config and S3.

## License
MIT License

## Author
[**Jatin Rautela**](https://github.com/JatinTTN)
