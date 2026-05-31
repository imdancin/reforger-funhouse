# Reforger Funhouse

This repository contains Terraform and Kubernetes GitOps configuration for a dedicated Arma Reforger game server running on AWS.

## What this project does

- provisions a single `c6i.xlarge` EC2 node in `us-west-2`
- attaches a permanent 50GB `gp3` root volume with `delete_on_termination = false`
- bootstraps K3s on the instance via `user_data`
- installs ArgoCD and applies a GitOps `Application` resource
- deploys an Arma Reforger game container through Helm-style manifests

## Current known issues

### 1. Backend state bucket

The Terraform backend references `your-unique-arma-tfstate-bucket` in:

- `providers.tf`
- `backend-resources.tf`

Note: In this environment the bucket name `your-unique-arma-tfstate-bucket` already exists and is currently storing Terraform state. You do not need to rename or recreate the bucket immediately — it is safe to continue using it.

If you later decide to change the bucket name, follow these steps to migrate state safely:

```powershell
terraform init -migrate-state
```

Or, if you prefer to only reconfigure backend settings without moving state, use:

```powershell
terraform init -reconfigure
```

### 2. ArgoCD repo access is private

The ArgoCD `Application` is configured to use:

- `https://github.com/imdancin/reforger-funhouse.git`

Because this repo is private, ArgoCD will not be able to sync until you add credentials with `argocd repo add`.

### 3. `main` branch must be present and pushed

The ArgoCD app uses:

- `targetRevision: main`

Make sure your `main` branch is pushed and contains `cluster-manifests/`.

### 4. SSM instance registration may lag

The bootstrap installs `amazon-ssm-agent`, but the instance must still successfully register with AWS Systems Manager before CLI commands will work.

Use:

```powershell
aws ssm describe-instance-information --filters Key=InstanceIds,Values=<instance-id> --profile reforger-admin --region us-west-2
```

If the response is empty, the instance is not yet SSM-connected.

## Helpful scripts

### `bootstrap.ps1`

This helper temporarily disables the S3 backend, creates the backend bucket and DynamoDB lock table locally, then migrates Terraform state to the cloud backend.

Usage:

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

### `install-argocd-cli.ps1`

Downloads a local `argocd.exe` binary for Windows so you can add the private GitHub repo to ArgoCD.

Usage:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-argocd-cli.ps1
```

After installing the CLI, add the private repo:

```powershell
.\argocd.exe login <argocd-server> --username admin --password <argocd-password>
.\argocd.exe repo add https://github.com/imdancin/reforger-funhouse.git --username <github-user> --password <personal-access-token>
```

## Deployment workflow

### 1. Initialize backend

```powershell
terraform init -reconfigure
```

### 2. Rotate the instance

```powershell
terraform apply -var "instance_count=0" -auto-approve
terraform apply -var "instance_count=1" -auto-approve
```

### 3. Verify K3s + ArgoCD

Use the SSM command flow to verify the node and ArgoCD app once the instance is running.

## File layout

- `providers.tf` — AWS provider and backend config
- `backend-resources.tf` — S3 bucket and DynamoDB lock table
- `networking.tf` — VPC, subnet, internet gateway, route table
- `security-groups.tf` — game ports and network boundaries
- `compute.tf` — EC2 instance definition and bootstrap user_data
- `iam.tf` — EC2 IAM role/profile for SSM
- `cluster-manifests/` — ArgoCD/Helm-style deployment manifests
- `install-argocd-cli.ps1` — ArgoCD CLI helper
- `bootstrap.ps1` — Terraform backend bootstrap helper

## Notes

- The current game deployment uses `hostNetwork: true` so the container binds directly to the EC2 host network.
- The charts currently use `game.publicAddress` and `game.rconPassword` from `cluster-manifests/values-freedomfighters.yaml`.
- If you change the backend or repo URL, remember to update both `providers.tf` and `backend-resources.tf` consistently.
