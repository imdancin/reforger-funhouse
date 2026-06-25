# Reforger Funhouse

Terraform + Kubernetes GitOps infrastructure for a dedicated Arma Reforger game server on AWS. One command to launch, one command to tear down.

## How it works

1. **Terraform** provisions a `c6i.xlarge` EC2 instance with a persistent 50GB EBS volume
2. **User data** bootstraps K3s and ArgoCD on the instance
3. **ArgoCD** pulls Helm manifests from this repo and deploys the game server pod
4. **External Secrets Operator** injects passwords from AWS Secrets Manager into the pod at runtime

The game server runs inside a container (`ghcr.io/acemod/arma-reforger`) on the K3s cluster with `hostNetwork: true`, binding directly to the EC2 host's network interfaces.

## Prerequisites

- AWS account with SSO configured
- AWS CLI configured with an SSO profile (e.g. `reforger-admin`)
- Terraform installed
- Python 3.13+ with [uv](https://docs.astral.sh/uv/) for the launch script
- SSH key at `~/.ssh/id_ed25519` (public key set in `terraform.tfvars`)
- A Route 53 hosted zone (optional, for custom DNS)

## Getting started (from scratch)

If you're forking this to host your own server:

### 1. Clone and install dependencies

```bash
git clone https://github.com/imdancin/reforger-funhouse.git
cd reforger-funhouse
uv sync
```

### 2. Set up AWS

You need an AWS account with:
- An IAM Identity Center (SSO) user or IAM user with admin-level access
- AWS CLI configured: `aws configure sso` or static credentials

### 3. Bootstrap the Terraform backend (one-time)

This creates the S3 bucket and DynamoDB table for remote state:

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

### 4. Create your `terraform.tfvars`

```hcl
instance_count    = 0          # start with 0, launch script sets to 1
enable_custom_dns = true       # set false if you don't have a Route 53 zone
domain_name       = "yourdomain.com"

ssh_allowed_cidr = "YOUR.PUBLIC.IP/32"   # find at https://checkip.amazonaws.com
ssh_public_key   = "ssh-ed25519 AAAA... your-email@example.com"

game_password       = "yourserverpassword"
game_admin_password = "youradminpassword"
rcon_password       = "yourrconpassword"
```

### 5. Generate an SSH key (if you don't have one)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519
```

Paste the contents of `~/.ssh/id_ed25519.pub` into `ssh_public_key` above.

### 6. Create ESO IAM credentials in SSM

The External Secrets Operator needs AWS credentials to pull secrets at runtime. Create an IAM user with read access to Secrets Manager and SSM, then store the keys:

```bash
aws ssm put-parameter --name /arma-reforger/eso-access-key-id --value "AKIA..." --type String
aws ssm put-parameter --name /arma-reforger/eso-secret-access-key --value "wJal..." --type SecureString
```

### 7. Customize game settings

Edit `cluster-manifests/values-freedomfighters.yaml` to set your scenario, mods, max players, etc.

### 8. Initialize Terraform and launch

```bash
terraform init
uv run python main.py
```

The script handles `terraform apply`, SSH connection, and log streaming automatically. First launch takes ~15 minutes (SteamCMD downloads ~10GB of game files).

### 9. Update repo references

In `compute.tf`, change the ArgoCD Application source to point to your fork:

```yaml
repoURL: 'https://github.com/YOUR-USER/reforger-funhouse.git'
```

## Quick start

### Launch the server

```bash
uv run python main.py
```

This runs the full pipeline:
1. `terraform apply -auto-approve` — provisions the EC2 instance
2. Waits for SSH to become available on the instance
3. Streams cloud-init bootstrap logs (K3s, ArgoCD, ESO installation)
4. Waits for the game server pod to reach Running state
5. Streams game server logs until you Ctrl+C

### Tear down the server

```bash
terraform apply -var "instance_count=0" -auto-approve
```

The EBS volume persists (game saves are kept). Next launch resumes from the existing save.

## Configuration

All sensitive values go in `terraform.tfvars` (gitignored):

```hcl
instance_count    = 1
enable_custom_dns = true
domain_name       = "imdancin.com"

ssh_allowed_cidr = "YOUR.IP.HERE/32"
ssh_public_key   = "ssh-ed25519 AAAA..."

game_password       = "yourpassword"
game_admin_password = "youradminpassword"
rcon_password       = "yourrconpassword"
```

Game settings (scenario, mods, player count) are in `cluster-manifests/values-freedomfighters.yaml`.

## Monitoring (Grafana + Prometheus)

The stack includes a full observability layer that deploys alongside the game server:

- **Prometheus** scrapes metrics from the K3s cluster (kubelet, kube-state-metrics, node-exporter) every 15-30 seconds and stores them with 15-day retention
- **Grafana** serves dashboards at `grafana.imdancin.com:3000` (or `<public_ip>:3000`)
- **Pre-built dashboards** for Node Exporter (CPU, memory, disk, network) and Kubernetes (pod status, resource usage, deployment replicas)

### Accessing Grafana

- **URL**: `http://grafana.imdancin.com:3000` (requires `enable_custom_dns = true`)
- **Username**: `admin`
- **Password**: stored in AWS Secrets Manager at `/arma-reforger/grafana-admin-password`

Set the Grafana admin password in your `terraform.tfvars`:

```hcl
grafana_admin_password = "your-secure-password"
```

### Architecture

Prometheus and its scrape targets (kube-state-metrics, node-exporter) are internal only — exposed via ClusterIP, no external ports opened. Only Grafana uses `hostNetwork` on port 3000 for external access.

Metrics storage lives at `/opt/arma-server-data/prometheus` on the same EBS volume as game data. With intermittent usage patterns (weekend sessions), actual disk consumption stays minimal since Prometheus auto-purges data older than 15 days.

### Disabling monitoring

Set `monitoring.enabled: false` in `cluster-manifests/values-freedomfighters.yaml` and push to `main`. ArgoCD will prune all monitoring resources on the next sync.

## Connecting to the server

- **Direct IP**: `<public_ip>:2001`
- **Join code**: printed in the server logs after startup
- **DNS**: `arma.imdancin.com` resolves to the Elastic IP (note: Reforger's direct join UI only accepts IPs, not hostnames)

## SSH access

```bash
ssh ubuntu@<public_ip>
```

SSH is only open when `ssh_allowed_cidr` is set in your tfvars. The security group rule is conditional.

## Server administration

### Check server status

```bash
ssh ubuntu@<public_ip>
sudo kubectl get pods -l app=arma-server
sudo kubectl logs -f -l app=arma-server -c reforger
```

### Force sync ArgoCD

```bash
sudo kubectl -n argocd patch app root-arma-app --type merge \
  -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{"revision":"HEAD"}}}'
```

### Reset game save (fresh start)

```bash
# Delete the save data on the PVC
sudo rm -rf /opt/arma-server-data/Profile/.db/FreedomFighters

# Restart the pod to pick up a clean state
sudo kubectl rollout restart deploy/arma-reforger
```

The server will start a new playthrough. Save data persists across instance stop/start cycles because the EBS volume has `delete_on_termination = false`.

### Restart the game server pod

```bash
sudo kubectl rollout restart deploy/arma-reforger
```

## Data persistence

- **EBS volume** (`delete_on_termination = false`) — survives instance termination
- **PVC** mounts `/opt/arma-server-data` with subPaths for Configs, Profile, and Workshop
- **`-loadSessionSave`** flag tells the server to resume from the last save on disk
- Setting `instance_count = 0` destroys the instance but the volume (and saves) persist

## Secrets management

| Secret | Storage | Injected via |
|--------|---------|--------------|
| Game password | AWS Secrets Manager | ExternalSecret → K8s Secret → env var |
| Admin password | AWS Secrets Manager | ExternalSecret → K8s Secret → env var |
| Grafana admin password | AWS Secrets MAnager | ExternalSecret → K8s Secret → env var |
| RCON password | AWS Secrets Manager | ExternalSecret → K8s Secret → env var |
| Public IP | SSM Parameter Store | ExternalSecret → K8s Secret → env var |
| Active scenario | SSM Parameter Store | Read by bootstrap user_data |

No secrets appear in version control. `terraform.tfvars` is gitignored.

## File layout

| Path | Purpose |
|------|---------|
| `main.py` | Launch automation script (terraform → SSH → log streaming) |
| `deploy_lambdas.py` | One-command deployment of all control-plane Lambda functions |
| `pyproject.toml` | Python project config (dependencies, pytest, Hypothesis settings) |
| `providers.tf` | AWS provider and S3 backend config |
| `backend-resources.tf` | S3 state bucket and DynamoDB lock table |
| `networking.tf` | VPC, subnet, internet gateway, route table |
| `security-groups.tf` | Game ports (UDP 2001, 1999), Grafana (TCP 3000), and conditional SSH |
| `compute.tf` | EC2 instance, EIP, bootstrap user_data, EBS volume |
| `control-plane.tf` | Discord control plane infrastructure (API GW, Lambdas, Step Functions, DynamoDB) |
| `iam.tf` | IAM roles for SSM and ESO |
| `secrets.tf` | Secrets Manager and SSM parameter resources |
| `route53.tf` | DNS A records for `arma.imdancin.com` and `grafana.imdancin.com` |
| `vars.tf` | Variable declarations |
| `outputs.tf` | Terraform outputs (instance ID, public IP, control plane endpoint) |
| `discord_control_plane/` | Python package: core logic, AWS adapters, Lambda handlers |
| `discord_control_plane/core/` | Pure logic (verification, authorization, presets, state decisions, idle accounting) |
| `discord_control_plane/adapters/` | AWS I/O (DynamoDB, SSM, Secrets Manager, Discord, GitHub) |
| `discord_control_plane/handlers/` | Lambda entry points (launch, orchestrator tasks, teardown) |
| `cluster-manifests/` | Helm chart deployed by ArgoCD |
| `cluster-manifests/values-freedomfighters.yaml` | Game config (scenario, mods, players) and monitoring/idle settings |
| `cluster-manifests/values-proceduralcombat.yaml` | Alternate preset (Procedural Combat scenario) |
| `cluster-manifests/templates/deployment.yaml` | Game server pod + PVC |
| `cluster-manifests/templates/idle-monitor-cronjob.yaml` | Idle_Monitor CronJob (player count sampling, auto-teardown) |
| `cluster-manifests/templates/monitoring-*.yaml` | Prometheus, Grafana, kube-state-metrics, node-exporter manifests |
| `cluster-manifests/templates/external-secrets.yaml` | ExternalSecret resources for game passwords |
| `tests/` | Property-based and unit tests (pytest + Hypothesis) |

## Discord Control Plane (Bot-Driven Launch)

The Discord integration lets authorized friends launch and manage the server directly from a Discord channel using `/launch` and `/status` slash commands — no AWS access needed.

### How it works

1. A Discord slash command posts a signed interaction to an API Gateway endpoint
2. The `Launch_Handler` Lambda verifies the Ed25519 signature, checks an allowlist, and transitions the server state to `LAUNCHING`
3. A Step Functions state machine dispatches a GitHub Actions workflow (`instance_count=1`), polls for readiness, and posts connection details back to Discord
4. An `Idle_Monitor` CronJob on the K3s cluster samples player count via RCON — after 30 minutes of zero players, it invokes the `Teardown_Handler` Lambda to destroy the instance

### Prerequisites

- A [Discord Application](https://discord.com/developers/applications) with:
  - A bot user (doesn't need to be in the guild — interactions work via webhook)
  - The **Interactions Endpoint URL** set to the API Gateway output (see below)
  - The application's **Public Key** (hex string from the General Information page)
- A fine-grained GitHub Personal Access Token with `contents: write` on this repo (for `repository_dispatch`)
- A Discord channel webhook URL for teardown/idle notifications

### Setup

#### 1. Deploy the control-plane infrastructure

Add the Discord public key to your `terraform.tfvars`:

```hcl
discord_app_public_key = "your-ed25519-public-key-hex"
```

Then apply:

```bash
terraform apply
```

Terraform outputs the interaction endpoint URL:

```
control_plane_api_endpoint = "https://xxxxxxxx.execute-api.us-west-2.amazonaws.com/interactions"
```

Set this as the **Interactions Endpoint URL** in your Discord app settings. Discord will send a `PING` to verify it — the Lambda responds with `PONG` automatically.

#### 2. Populate secrets

```bash
# GitHub dispatch token
aws secretsmanager put-secret-value \
  --secret-id /arma-reforger/github-dispatch-token \
  --secret-string "ghp_your_fine_grained_token"

# Discord channel webhook URL (for teardown notifications)
aws secretsmanager put-secret-value \
  --secret-id /arma-reforger/discord-channel-webhook-url \
  --secret-string "https://discord.com/api/webhooks/..."
```

#### 3. Configure the allowlist

Add authorized Discord user IDs and/or role IDs to the SSM parameter:

```bash
aws ssm put-parameter \
  --name /arma-reforger/discord-allowlist \
  --type String \
  --overwrite \
  --value '{"user_ids": ["111111111111111111"], "role_ids": ["222222222222222222"]}'
```

You can find user IDs by enabling Developer Mode in Discord (Settings → Advanced) and right-clicking a user.

#### 4. Register the slash commands

Run the registration script (one-time, or after updating presets):

```bash
uv run python -c "
from discord_control_plane.handlers.registration import build_launch_command_payload, build_status_command_payload
import json
print('=== /launch ===')
print(json.dumps(build_launch_command_payload(), indent=2))
print('=== /status ===')
print(json.dumps(build_status_command_payload(), indent=2))
"
```

POST each command payload to `https://discord.com/api/v10/applications/{APP_ID}/commands` with your bot token as a Bearer header. Or use a tool like `curl`:

```bash
curl -X POST "https://discord.com/api/v10/applications/YOUR_APP_ID/commands" \
  -H "Authorization: Bot YOUR_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d @command.json
```

#### 5. Deploy the Lambda code

The Terraform creates Lambda functions with placeholder code. Deploy the actual handlers with the included script:

```bash
uv run python deploy_lambdas.py
```

This packages the `discord_control_plane/` module with its dependencies and updates all 8 Lambda functions in one shot. Options:

```bash
uv run python deploy_lambdas.py --profile reforger-admin --region us-west-2
```

The script updates both the code and the handler path for each function. Run it again after any code changes to the control plane.

### Usage

From any Discord channel where the bot is accessible:

```
/launch                          # launches with default preset (Freedom Fighters)
/launch preset:proceduralcombat  # launches with Procedural Combat preset
/status                          # check current server status
```

The bot responds to `/launch` with:
- A deferred acknowledgement (instantly)
- A "launch started" message with the selected preset
- Connection details (`<public_ip>:2001`) once the server is ready
- A timeout or failure message if something goes wrong

The bot responds to `/status` with:
- Current server state (offline, launching, running, tearing down)
- Connection details (`<ip>:<port>`) when the server is running
- The active preset name

### Available presets

| Preset | Values file | Description |
|--------|-------------|-------------|
| `freedomfighters` (default) | `values-freedomfighters.yaml` | Freedom Fighters scenario |
| `proceduralcombat` | `values-proceduralcombat.yaml` | Procedural Combat scenario |

### Idle auto-teardown

The `Idle_Monitor` runs every minute on the cluster. If the player count stays at zero for 30 minutes (configurable via `idleMonitor.idleThresholdSeconds` in the values files), it invokes the `Teardown_Handler` which:

1. Dispatches `instance_count=0` to destroy the EC2 instance
2. Posts a teardown notification to the configured Discord webhook
3. The EBS game-data volume is preserved (saves are safe)

### Running tests

The control plane has a comprehensive test suite including property-based tests:

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

This runs ~200 tests covering signature verification, authorization, preset resolution, state machine logic, idle accounting, handler wiring, and Terraform structural assertions.

### Troubleshooting

- **"Invalid signature" / 401 on Discord endpoint verification**: Double-check the `discord_app_public_key` in your tfvars matches the hex public key from the Discord Developer Portal (General Information → Public Key).
- **Launch times out**: Check the GitHub Actions workflow is triggering correctly. Verify the dispatch token has `contents: write` permission on the repo.
- **Allowlist denials**: Confirm user/role IDs in the SSM parameter. IDs are snowflakes (18-digit numbers).
- **Idle_Monitor not firing**: Ensure `idleMonitor.enabled: true` in your values file and that the RCON password secret (`arma-rcon-secret`) exists on the cluster.

## Known quirks

- **First launch is slow** (~15 min) — SteamCMD downloads ~10GB of game files. Subsequent launches reuse the cached files on the PVC.
- **Navmesh warnings** in server logs are cosmetic — missing vehicle pathfinding tiles don't affect gameplay.
- **ArgoCD syncs every 3 minutes** — push a change to `main` branch and wait, or force sync manually.
