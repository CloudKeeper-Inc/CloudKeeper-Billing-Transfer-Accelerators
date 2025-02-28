# Multi-Account AWS GuardDuty Replication

## Overview
This project automates the replication of an AWS Organizations GuardDuty into a multi-account AWS GuardDuty setup. It ensures that GuardDuty logs from member accounts are centrally collected in the master account, maintaining compliance and visibility across the AWS organization.

## Features
- Detects if AWS Organizations GuardDuty is enabled.
- Replicates the organization-level GuardDuty setup in a multi-account structure.
- Configures AWS GuardDuty to pull logs from member accounts into the master account.

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
   cd AWS-GuardDuty
   ```
2. Install dependencies:
   ```sh
   pip install boto3
   ```

## Usage
Run the script to initiate the multi-account GuardDuty setup:
```sh
python3 main.py
```

## How It Works
1. **Checks AWS Organizations GuardDuty:**
   - Fetches details of the existing organization GuardDuty.
2. **Replicates the GuardDuty configurations:**
3. **Enables Cross-Account Logging:**
   - Updates member accounts to send logs to the master account.
   - Grants required permissions to the master account's IAM role.

## Troubleshooting
- **Profile Not Found Error:** Ensure the AWS profile is correctly set up:
  ```sh
  aws configure list-profiles
  ```
- **Permission Denied Errors:** Verify IAM role permissions for GuardDuty.

## License
MIT License

## Author
[**Jatin Rautela**](https://github.com/JatinTTN)
