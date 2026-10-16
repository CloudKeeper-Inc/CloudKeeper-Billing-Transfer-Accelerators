# Multi-Account AWS Organization Services Accelerators

## Overview

This project automates the setup of AWS Organization-wide services in a multi-account setup, from the organization's management account. Each accelerator is a standalone Python script that only needs `boto3` and one AWS CLI profile on your machine. The setups it creates keep working when accounts leave the organization, which makes them suitable for billing transfers.

## Accelerators

- **[AWS-CloudTrail](AWS-CloudTrail/):** For organizations where an organization trail isn't an option. A one-time script that creates an S3 bucket in the management account and a trail in every account of the organization (management events, read and write) that delivers to it. Reaches member accounts through a temporary StackSet role, which it deletes at the end of the run.

## Prerequisites

Before you begin, ensure you have the following prerequisites in place:

1. **Python**:

   - Python 3.8 or newer. [Install Python](https://www.python.org/downloads/)
2. **AWS CLI**:

   - Install AWS CLI if not already installed. Follow the [AWS CLI installation guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html).
3. **Configuring AWS CLI**:

   - Ensure that you have an AWS CLI profile for an **administrator identity in the organization's management account**, for example the `AdministratorAccess` permission set of IAM Identity Center. One profile is enough; you don't need a profile per account.
   - Confirm that the profile works:
     ```sh
     aws sts get-caller-identity --profile <management-account-admin-profile>
     ```

Each accelerator's README lists its own additional prerequisites.

## Installation

1. Clone the repository:
   ```sh
   git clone https://github.com/CloudKeeper-Inc/CloudKeeper-Billing-Transfer-Accelerators.git
   ```
2. Install dependencies:
   ```sh
   cd CloudKeeper-Billing-Transfer-Accelerators/<accelerator>
   pip install -r requirements.txt
   ```

## Usage

Every accelerator supports read-only pre-flight checks and a read-only plan before it changes anything:

```sh
python3 <script>.py --profile <management-account-admin-profile> --check     # read-only pre-flight checks
python3 <script>.py --profile <management-account-admin-profile> --dry-run   # read-only plan
python3 <script>.py --profile <management-account-admin-profile>             # apply
```

Every accelerator:

- runs from the **organization's management account** with one administrator profile
- reaches member accounts through a temporary, least-privilege role deployed with a CloudFormation StackSet, and deletes the StackSet at the end of every run
- uses default resource names that contain the keyword `accelerator`, unless you choose your own

See each accelerator's README for its parameters, output, errors and troubleshooting.

## Defunct Accelerators

The earlier Terraform-based accelerators (CloudTrail, Config, GuardDuty, SecurityHub) are in [`tf-defunct/`](tf-defunct/). They are kept for reference only and are no longer maintained.

## Troubleshooting

- **Profile Not Found Error:** Ensure the AWS profile is correctly set up:
  ```sh
  aws configure list-profiles
  ```
- **Token has expired:** Log in again with `aws sso login --profile <profile>`.
- **Permission Denied Errors:** Use an administrator identity of the management account, and run the accelerator's `--check` mode to see which policy denies the call.

## License

MIT License

## Authors

- [**Jatin Rautela**](https://github.com/JatinTTN): Terraform accelerators (now in `tf-defunct/`)
- [**Viransh Bhardwaj**](https://github.com/CloudSpyV): AWS-CloudTrail accelerator
