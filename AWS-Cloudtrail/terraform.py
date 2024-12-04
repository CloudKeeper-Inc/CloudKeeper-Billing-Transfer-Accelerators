import os
import subprocess
import shutil, json

def terraform_apply(account_id, region, terraform_state_file):
    # Check if the Terraform state file exists
    folder_path = os.path.dirname(terraform_state_file.split('/')[0] + '/')
    
    terraform_initialized = os.path.isfile(terraform_state_file)

    if not terraform_initialized:
        print("Terraform is not initialized. Initializing...")
        subprocess.run(["terraform", "init"], cwd=folder_path)

    print(f"Processing region {region} in account {account_id}...")

    workspace_name = f'{account_id}.{region}'

    # Construct the filename for the .tfvars file
    tfvars_file = f"terraform.{workspace_name}.tfvars"

    # Check if a .tfvars file exists for the current workspace
    if not os.path.isfile(folder_path + '/' + tfvars_file):
        print(f"Error: Terraform .tfvars file '{tfvars_file}' not found for workspace '{workspace_name}'")
        exit(1)

    # Check if the workspace exists
    try:
        result = subprocess.run(
            ["terraform", "workspace", "select", workspace_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,  # This ensures that the output is returned as a string
            cwd=folder_path  # Set the working directory to the folder path
        )
        print(f"Workspace '{workspace_name}' found. Exiting...")
    except subprocess.CalledProcessError as e:
        print(f"Error selecting workspace '{workspace_name}': {e.stderr}")
        result = subprocess.run(
            ["terraform", "workspace", "new", workspace_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,  # This ensures that the output is returned as a string
            cwd=folder_path  # Set the working directory to the folder path
        )
        result = subprocess.run(
            ["terraform", "workspace", "select", workspace_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,  # This ensures that the output is returned as a string
            cwd=folder_path  # Set the working directory to the folder path
        )
        print(f"Workspace '{workspace_name}' found. Applying Terraform...")
    except FileNotFoundError as e:
        print(f"Terraform command not found: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

    try:
        if terraform_state_file == 'import/.terraform/terraform.tfstate':
            # Run the Terraform plan command with the generate-config-out option
            try:
                plan_result = subprocess.run(
                    ["terraform", "plan", "-input=false", f"-generate-config-out=generated.{workspace_name}.tf", "-var-file=" + tfvars_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=True,
                    cwd=folder_path  # Set the working directory to the folder path
                )
                print("Terraform plan executed successfully.")
                print(plan_result.stdout)

            except subprocess.CalledProcessError as e:
                print("Error executing Terraform plan:")
                # print(e.stderr)  # Log the error output from the Terraform command
                # Continue without exiting

            # Define the source and target paths
            source_path = os.path.join(folder_path, f'generated.{workspace_name}.tf')
            temp_folder = 'masterAccount'  
            moved_file_path = os.path.join(temp_folder, f'cloudtrail.tf')

            # Move the generated file
            try:
                shutil.move(source_path, moved_file_path)
                print(f"Moved file to {moved_file_path} successfully.")
            except Exception as e:
                print(f"Error moving file: {e}")

            # Now copy the moved file to another destination
            target_folder = 'memberAccount'
            destination_path = os.path.join(target_folder, f'cloudtrail.tf')

            try:
                shutil.copy(moved_file_path, destination_path)
                print(f"Copied file to {destination_path} successfully.")
            except Exception as e:
                print(f"Error copying file: {e}")

            return

        # elif terraform_state_file == 'masterAccount/.terraform/terraform.tfstate':
        #     # plan_command = [
        #     #     "terraform", "plan", "-input=false",
        #     #     f"-generate-config-out=generated.{workspace_name}.tf",
        #     #     "-var-file=" + tfvars_file
        #     # ]
        #     # plan_result = subprocess.run(plan_command, 
        #     #                          stdout=subprocess.PIPE, 
        #     #                          stderr=subprocess.PIPE, 
        #     #                          check=True, text=True, cwd=folder_path)
        #     # print("Terraform plan executed successfully.")
        #     # print(plan_result.stdout)  # Print the output of the terraform plan command
        #     generated_tf_file = f"masterAccount/generated.{workspace_name}.tf"

        #     # Check if the generated file exists before applying
        #     if not os.path.exists(generated_tf_file):
        #         print(f"Generated file {generated_tf_file} does not exist.")
        #         return

    except Exception as e:
        print(f"An unexpected error occurred: {e}")

    # Apply Terraform with the matching .tfvars file
    subprocess.run(["terraform", "apply", "-auto-approve","-var-file=" + tfvars_file], check=True, cwd=folder_path)
    # Retrieve the output in JSON format
    output = subprocess.run(
        ["terraform", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
        cwd=folder_path
    )

    # Parse JSON output
    outputs = json.loads(output.stdout)

    # Save the output to a JSON file
    output_file = f"{workspace_name}_output_values.json"
    with open(output_file, "w") as file:
        json.dump(outputs, file, indent=4)

    print(f"Output saved to {output_file}")
    return outputs
