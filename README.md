# Reforger Funhouse

This repository contains Terraform and Kubernetes GitOps configuration for a dedicated Arma Reforger game server running on AWS.

## What this project does

- provisions a single `c6i.xlarge` EC2 node in `us-west-2`
- attaches a permanent 50GB `gp3` root volume with `delete_on_termination = false`
- bootstraps K3s on the instance via `user_data`
- installs ArgoCD and applies a GitOps `Application` resource
- deploys an Arma Reforger game container through Helm-style manifests

## ⚠️ Before Making This Repository Public

**Do not change this repository's visibility to public until all of the following steps are complete.**

This repository has historically contained hardcoded secrets including RCON passwords, EC2 IP addresses, and access tokens. Even if those values have been removed from the current working tree, they may still exist in Git history and will be exposed the moment the repo goes public.

### What must be done before going public

1. **Remove all secrets from the working tree** — verify no plaintext credentials remain in any tracked file. This is covered by **Requirement 1: Remove Hardcoded Secrets from the Repository**. Specifically confirm:
   - `cluster-manifests/values-freedomfighters.yaml` contains no `rconPassword` or `publicAddress` fields
   - `terraform.tfvars` is not tracked by Git (run `git rm --cached terraform.tfvars` if it is)
   - No hardcoded IPs, tokens, or passwords appear in any `.tf`, `.yaml`, or `.ps1` file

2. **Scrub Git history** — remove the sensitive file from all past commits using `git filter-repo`:

   ```bash
   git filter-repo --path cluster-manifests/values-freedomfighters.yaml --invert-paths
   ```

   This rewrites history to exclude `cluster-manifests/values-freedomfighters.yaml` from every commit. After running this command, force-push to all remotes:

   ```bash
   git push origin --force --all
   git push origin --force --tags
   ```

   > **Note:** `git filter-repo` must be installed separately (`pip install git-filter-repo`). All collaborators must re-clone the repository after a history rewrite.

3. **Rotate any exposed secrets** — treat any credential that was ever committed as compromised. Rotate the RCON password, revoke any exposed tokens, and release/reassign any IP addresses that were hardcoded.

4. **Verify `.gitignore`** — confirm `*.tfvars` and `*.tfvars.json` are listed so secrets files are never accidentally committed again.

Only after completing all four steps is it safe to make this repository public.

---

## Setup Notes

### Untracking `terraform.tfvars`

If `terraform.tfvars` is currently tracked by Git (i.e., it was committed before the `*.tfvars` entry was added to `.gitignore`), run the following command to stop tracking it without deleting the local file:

```bash
git rm --cached terraform.tfvars
git commit -m "chore: untrack terraform.tfvars"
```

After this, `terraform.tfvars` will be ignored by Git going forward. Verify with `git status` — the file should no longer appear as a tracked or modified file.

---

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

### 2. ArgoCD repo access (public — no credentials required)

The ArgoCD `Application` is configured to use:

- `https://github.com/imdancin/reforger-funhouse.git`

This repository is public, so ArgoCD can clone it without any credentials. No `argocd repo add` step is required after bootstrapping.

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
.\argocd.exe login <argocd-server> --username admin --password <argocd-password> --insecure
.\argocd.exe repo add https://github.com/imdancin/reforger-funhouse.git --username <github-user> --password <personal-access-token> --name reforger-funhouse --insecure
```

Or use the helper script once `argocd.exe` is available:

```powershell
powershell -ExecutionPolicy Bypass -File .\add-argocd-repo.ps1 \
  -ArgocdServer <argocd-server> \
  -AdminPassword <admin-password> \
  -GithubUser <github-user> \
  -GithubToken <personal-access-token>
```

### `add-argocd-portforward.ps1`

Starts a local port-forward to the ArgoCD server so you can open the ArgoCD UI in your browser.

Usage:

```powershell
powershell -ExecutionPolicy Bypass -File .\add-argocd-portforward.ps1
```

By default this forwards:

- `localhost:8080` -> `argocd-server:443`

Then visit:

```text
https://localhost:8080
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

Example validation steps:

```powershell
aws ssm describe-instance-information --filters Key=InstanceIds,Values=<instance-id> --profile reforger-admin --region us-west-2
```

Once the instance is visible in SSM, run:

```powershell
aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript --comment "Check k3s and ArgoCD" --parameters commands="/usr/local/bin/kubectl get nodes && /usr/local/bin/kubectl get pods -n argocd && /usr/local/bin/kubectl get applications -n argocd" --profile reforger-admin --region us-west-2
```
add-argocd-portforward.ps1` — local ArgoCD UI port-forward helper
- `
Then verify the game workload and local storage:

```powershell
aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript --parameters commands="/usr/local/bin/kubectl get pv,pvc && /usr/local/bin/kubectl get pods -n default" --profile reforger-admin --region us-west-2
```

If the ArgoCD application is not syncing, add the private repo credentials with `argocd repo add` before retrying.

## File layout

- `providers.tf` — AWS provider and backend config
- `backend-resources.tf` — S3 bucket and DynamoDB lock table
- `networking.tf` — VPC, subnet, internet gateway, route table
- `security-groups.tf` — game ports and network boundaries
- `compute.tf` — EC2 instance definition and bootstrap user_data
- `iam.tf` — EC2 IAM role/profile for SSM and GitHub Actions least-privilege policy
- `secrets.tf` — Secrets Manager and SSM Parameter Store resource provisioning
- `cluster-manifests/` — ArgoCD/Helm-style deployment manifests
- `cluster-manifests/templates/external-secrets.yaml` — ESO SecretStore and ExternalSecret resources
- `install-argocd-cli.ps1` — ArgoCD CLI helper
- `add-argocd-repo.ps1` — private GitHub repo registration helper for ArgoCD
- `bootstrap.ps1` — Terraform backend bootstrap helper

## Notes

- The current game deployment uses `hostNetwork: true` so the container binds directly to the EC2 host network.
- The charts currently use `game.publicAddress` and `game.rconPassword` from `cluster-manifests/values-freedomfighters.yaml`.
- If you change the backend or repo URL, remember to update both `providers.tf` and `backend-resources.tf` consistently.
