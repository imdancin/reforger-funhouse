# Reforger Funhouse

Terraform + Kubernetes GitOps infrastructure for a dedicated Arma Reforger game server on AWS. One command to launch, one command to tear down.

**Support server costs:** The server runs on AWS and costs real money. If you're enjoying game nights and want to chip in for hosting costs, tips are appreciated: [ko-fi.com/imdancin](https://ko-fi.com/imdancin)

## How it works

1. **Discord `/launch`** triggers the control plane — a Lambda verifies the user, transitions state to LAUNCHING, and starts a Step Functions orchestrator
2. **Step Functions** resets SSM bootstrap-status, dispatches a `repository_dispatch` event to GitHub
3. **GitHub Actions** runs `terraform apply` with `instance_count=1` via OIDC-based AWS credentials
4. **Terraform** provisions a `c6i.xlarge` EC2 instance with a persistent 20GB EBS data volume
5. **User data** bootstraps K3s and ArgoCD on the instance, writes `ready:<timestamp>` to SSM when done
6. **The orchestrator** polls SSM bootstrap-status every 30 seconds until ready, then posts connection details to Discord
7. **ArgoCD** pulls Helm manifests from this repo and deploys the game server pod
8. **External Secrets Operator** injects passwords from AWS Secrets Manager into the pod at runtime
9. **Metrics Exporter** samples player count via BattlEye RCON (UDP) and exposes it as the `arma_connected_players` Prometheus metric; teardown is manual via `/stop` (dispatches `instance_count=0`)

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
aws_profile       = "reforger-admin"  # your local AWS CLI profile (leave empty for CI)
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
terraform init -backend-config="profile=reforger-admin"
uv run python main.py
```

The script handles `terraform apply`, SSH connection, and log streaming automatically. First launch takes ~15 minutes (SteamCMD downloads ~10GB of game files).

### 9. Update repo references

In `compute.tf`, change the ArgoCD Application source to point to your fork:

```yaml
repoURL: 'https://github.com/YOUR-USER/reforger-funhouse.git'
```

## Quick start

### Launch the server (Discord)

From any Discord channel where the bot is accessible:

```
/launch                          # launches with default preset (Freedom Fighters)
/launch preset:proceduralcombat  # launches with Procedural Combat preset
/stop                            # stop the server and tear down the instance
```

This triggers the full pipeline: SSM reset → GitHub dispatch → Terraform apply → EC2 boot → bootstrap → readiness check → Discord posts connection details.

### Launch the server (manual)

```bash
uv run python main.py
```

This runs `terraform apply` directly from your machine (bypasses the Discord/GitHub Actions flow).

### Tear down the server

Teardown is manual only (via `/stop` in Discord, or directly with Terraform):

```bash
terraform apply -var "instance_count=0" -auto-approve
```

The EBS volume persists (game saves are kept). Next launch resumes from the existing save.

## Configuration

All sensitive values go in `terraform.tfvars` (gitignored):

```hcl
aws_profile       = "reforger-admin"
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
- **Pre-built dashboards** for Server Overview (CPU, memory, disk, network, connected players) and Kubernetes (pod status, resource usage, deployment replicas)

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
| `Dockerfile.metrics-exporter` | Container image for the RCON metrics-exporter pod (Python + BERCon client + Prometheus) |
| `pyproject.toml` | Python project config (dependencies, pytest, Hypothesis settings) |
| `providers.tf` | AWS provider and S3 backend config |
| `.github/workflows/terraform-apply.yml` | GitHub Actions workflow triggered by `repository_dispatch` to run `terraform apply` |
| `.github/workflows/build-metrics-exporter.yml` | GitHub Actions workflow to build and push the metrics-exporter container image to GHCR |
| `.github/workflows/deploy-lambdas.yml` | GitHub Actions workflow to deploy control-plane Lambda code on merge to main |
| `backend-resources.tf` | S3 state bucket and DynamoDB lock table |
| `networking.tf` | VPC, subnet, internet gateway, route table |
| `security-groups.tf` | Game ports (UDP 2001, UDP 1999 RCON), Grafana (TCP 3000), and conditional SSH |
| `compute.tf` | EC2 instance, EIP, bootstrap user_data, EBS volume |
| `control-plane.tf` | Discord control plane infrastructure (API GW, Lambdas, Step Functions, DynamoDB) |
| `iam.tf` | IAM roles for SSM and ESO |
| `secrets.tf` | Secrets Manager and SSM parameter resources |
| `route53.tf` | DNS A records for `arma.imdancin.com` and `grafana.imdancin.com` |
| `vars.tf` | Variable declarations |
| `outputs.tf` | Terraform outputs (instance ID, public IP, control plane endpoint) |
| `discord_control_plane/` | Python package: core logic, AWS adapters, Lambda handlers |
| `discord_control_plane/core/` | Pure logic (verification, authorization, presets, state decisions) |
| `discord_control_plane/adapters/` | AWS I/O (DynamoDB, SSM, Secrets Manager, Discord, GitHub, BERCon RCON) |
| `discord_control_plane/handlers/` | Lambda entry points (launch, orchestrator tasks, teardown) and the metrics exporter |
| `cluster-manifests/` | Helm chart deployed by ArgoCD |
| `cluster-manifests/values-freedomfighters.yaml` | Game config (scenario, mods, players) and monitoring/metrics-exporter settings |
| `cluster-manifests/values-proceduralcombat.yaml` | Alternate preset (Procedural Combat scenario) |
| `cluster-manifests/templates/deployment.yaml` | Game server pod + PVC |
| `cluster-manifests/templates/metrics-exporter-*.yaml` | Standalone metrics-exporter Deployment + Service (player count sampling via BattlEye RCON) |
| `cluster-manifests/templates/monitoring-*.yaml` | Prometheus, Grafana, kube-state-metrics, node-exporter manifests |
| `cluster-manifests/templates/external-secrets.yaml` | ExternalSecret resources for game passwords |
| `tests/` | Property-based and unit tests (pytest + Hypothesis) |

## Discord Control Plane (Bot-Driven Launch)

The Discord integration lets authorized friends launch and manage the server directly from a Discord channel using `/launch`, `/status`, and `/stop` slash commands — no AWS access needed.

### How it works

1. A Discord slash command posts a signed interaction to an API Gateway endpoint
2. The `Launch_Handler` Lambda verifies the Ed25519 signature, checks an allowlist, and transitions the server state to `LAUNCHING`
3. A Step Functions state machine resets SSM bootstrap-status to `"provisioning"`, then dispatches a `repository_dispatch` event to GitHub
4. A GitHub Actions workflow (`terraform-apply.yml`) runs `terraform apply` with `instance_count=1` using OIDC-based AWS credentials
5. The orchestrator polls SSM bootstrap-status every 30 seconds until the EC2 instance reports ready
6. Once ready, `MarkRunning` posts connection details back to Discord and transitions state to `RUNNING`
7. A standalone metrics-exporter pod samples player count via BattlEye RCON (UDP) and exposes it as the `arma_connected_players` Prometheus gauge; the server is stopped manually via `/stop` (no automatic idle teardown)

### Prerequisites

- A [Discord Application](https://discord.com/developers/applications) with:
  - A bot user (doesn't need to be in the guild — interactions work via webhook)
  - The **Interactions Endpoint URL** set to the API Gateway output (see below)
  - The application's **Public Key** (hex string from the General Information page)
- A fine-grained GitHub Personal Access Token with `contents: write` on this repo (for `repository_dispatch`)
- A Discord channel webhook URL for teardown notifications

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

#### 2. Set up GitHub Actions secrets

The `/launch` command triggers a GitHub Actions workflow to run Terraform. You need:

| Secret | Value |
|--------|-------|
| `AWS_ROLE_ARN` | Full ARN of the OIDC deployer role (e.g., `arn:aws:iam::123456789:role/github-actions-arma-deployer`) |
| `TERRAFORM_TFVARS` | Full contents of your `terraform.tfvars` file (with `aws_profile = ""`) |

The workflow uses OIDC federation — no static AWS keys needed. The IAM role's trust policy must allow `repo:YOUR-ORG/reforger-funhouse:*`.

#### 3. Populate AWS secrets

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

Generate the command payloads:

```bash
uv run python -c "
from discord_control_plane.handlers.registration import (
    build_launch_command_payload,
    build_status_command_payload,
    build_stop_command_payload,
)
import json
print('=== /launch ===')
print(json.dumps(build_launch_command_payload(), indent=2))
print('=== /status ===')
print(json.dumps(build_status_command_payload(), indent=2))
print('=== /stop ===')
print(json.dumps(build_stop_command_payload(), indent=2))
"
```

Register commands to a specific guild (recommended — instant availability, no propagation delay). You'll need your guild ID (enable Developer Mode in Discord, right-click your server name → Copy Server ID).

**Important:** Discord's API requires a `User-Agent` header in the format `DiscordBot (url, version)` or requests may be blocked by Cloudflare (error 40333).

PowerShell example (guild-scoped):

```powershell
$token = "YOUR_BOT_TOKEN"
$appId = "YOUR_APP_ID"
$guildId = "YOUR_GUILD_ID"
$headers = @{
    "Authorization" = "Bot $token"
    "User-Agent" = "DiscordBot (https://github.com/imdancin/reforger-funhouse, 1.0)"
}

# /launch
$body = '{"name":"launch","description":"Launch the Arma Reforger server with a chosen preset","type":1,"options":[{"name":"preset","description":"Server preset to launch","type":3,"required":false,"choices":[{"name":"Freedom Fighters","value":"freedomfighters"},{"name":"Procedural Combat","value":"proceduralcombat"},{"name":"Freedom Fighters - Modern Finland","value":"freedomfightersmodernfinland"}]}]}'
Invoke-RestMethod -Method Post -Uri "https://discord.com/api/v10/applications/$appId/guilds/$guildId/commands" -Headers $headers -Body $body -ContentType "application/json"

# /status
$body = '{"name":"status","description":"Check the current Arma Reforger server status","type":1,"options":[]}'
Invoke-RestMethod -Method Post -Uri "https://discord.com/api/v10/applications/$appId/guilds/$guildId/commands" -Headers $headers -Body $body -ContentType "application/json"

# /stop
$body = '{"name":"stop","description":"Stop the Arma Reforger server and tear down the instance","type":1,"options":[]}'
Invoke-RestMethod -Method Post -Uri "https://discord.com/api/v10/applications/$appId/guilds/$guildId/commands" -Headers $headers -Body $body -ContentType "application/json"
```

Or with `curl.exe` (on Windows) / `curl` (on Linux/macOS):

```bash
curl -X POST "https://discord.com/api/v10/applications/YOUR_APP_ID/guilds/YOUR_GUILD_ID/commands" \
  -H "Authorization: Bot YOUR_BOT_TOKEN" \
  -H "User-Agent: DiscordBot (https://github.com/imdancin/reforger-funhouse, 1.0)" \
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

After the initial deploy, a CI workflow (`.github/workflows/deploy-lambdas.yml`) automatically redeploys the Lambdas whenever changes to `discord_control_plane/` are merged to `main`.

### Usage

From any Discord channel where the bot is accessible:

```
/launch                          # launches with default preset (Freedom Fighters)
/launch preset:proceduralcombat  # launches with Procedural Combat preset
/status                          # check current server status
/stop                            # manually stop the server and tear down the instance
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

The bot responds to `/stop` with:
- Confirmation that teardown has been initiated
- An error if the server is not in a stoppable state (e.g. already offline)
- Requires the same allowlist authorization as `/launch`

### Available presets

| Preset | Values file | Description |
|--------|-------------|-------------|
| `freedomfighters` (default) | `values-freedomfighters.yaml` | Freedom Fighters scenario |
| `proceduralcombat` | `values-proceduralcombat.yaml` | Procedural Combat scenario |

### Server teardown

Teardown is manual only — run `/stop` in Discord when you're done playing. The `Teardown_Handler`:

1. Dispatches `instance_count=0` to destroy the EC2 instance
2. Posts a teardown notification to the configured Discord webhook
3. The EBS game-data volume is preserved (saves are safe)

### Player count metrics

A standalone `metrics-exporter` Deployment runs alongside the game server pod (same node, `hostNetwork: true`, so it can reach RCON on `127.0.0.1:1999`), sampling RCON every 60 seconds (configurable via `metricsExporter.sampleIntervalSeconds` in the values files) and exposing the `arma_connected_players` Prometheus gauge for the standard monitoring stack (Prometheus/Grafana) to scrape. Keeping it as its own pod (rather than a sidecar) means the game server pod only ever contains game server activity, and the exporter can be restarted, debugged, or have its logs inspected independently. The image (`ghcr.io/imdancin/reforger-funhouse-metrics-exporter:latest`) is built automatically by the `build-metrics-exporter.yml` workflow when files in `discord_control_plane/` are pushed to `main`. It uses the BattlEye RCON (BERCon) UDP protocol to query player count — this is the same protocol used by tools like BERcon and BattleMetrics.

### Running tests

The control plane has a comprehensive test suite including property-based tests:

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

This runs ~230 tests covering signature verification, authorization, preset resolution, state machine logic, handler wiring, and Terraform/Helm structural assertions.

### Troubleshooting

- **"Invalid signature" / 401 on Discord endpoint verification**: Double-check the `discord_app_public_key` in your tfvars matches the hex public key from the Discord Developer Portal (General Information → Public Key).
- **"Application did not respond"**: The launch handler Lambda is hitting Discord's 3-second response deadline. This usually happens on the first invocation after a deploy (cold start). Try again — subsequent invocations use SnapStart and respond faster.
- **Launch times out**: Check the GitHub Actions workflow is triggering correctly. Verify the dispatch token has `contents: write` permission on the repo. Check the workflow run at `https://github.com/YOUR-ORG/reforger-funhouse/actions`.
- **GitHub Actions fails with "failed to get shared config profile"**: The `terraform.tfvars` in your `TERRAFORM_TFVARS` secret should have `aws_profile = ""` (not your local profile name).
- **GitHub Actions fails with IAM permission errors**: The OIDC deployer role needs permissions for all services Terraform manages. Check the inline policy on `github-actions-arma-deployer`.
- **Orchestrator SUCCEEDED in ~6 seconds without provisioning**: This was the original bug. Ensure you've deployed the latest Lambda code (`python deploy_lambdas.py`) which includes the SSM bootstrap-status reset.
- **Allowlist denials**: Confirm user/role IDs in the SSM parameter. IDs are snowflakes (18-digit numbers).
- **`arma_connected_players` not showing up in Grafana**: Ensure the RCON password secret (`arma-rcon-secret`) exists on the cluster. The game server deployment must include `RCON_ADDRESS=0.0.0.0`, `RCON_PERMISSION=admin`, and `-rcon` in `ARMA_PARAMS` for BattlEye RCON to actually bind. Verify RCON is listening with `ss -ulnp | grep 1999` (it's UDP, not TCP). Check the metrics-exporter pod's logs (`kubectl logs deploy/metrics-exporter`) for RCON sampling errors.

## Adding a new scenario (contributing a preset)

Want to add a new game mode? Here's the full process:

### 1. Create the values file

Copy an existing preset as a starting point:

```bash
cp cluster-manifests/values-freedomfighters.yaml cluster-manifests/values-yourscenario.yaml
```

Edit the `game:` section with your scenario details:

```yaml
game:
  name: "Dancins Reforger Funhouse - Your Scenario Name"
  maxPlayers: 10
  scenarioId: "{SCENARIO_GUID}Missions/YourScenario/Map.conf"
  modsList: "MOD_ID_1,MOD_ID_2,MOD_ID_3"
```

You can find the `scenarioId` in the Arma Reforger Workbench or from the mod's workshop page. The `modsList` is a comma-separated list of mod GUIDs (the hex IDs from the Workshop URL or `ServerData.json`).

Leave the `metricsExporter:` and `monitoring:` sections unchanged — just copy them from an existing file.

### 2. Register the preset in code

Add your preset to `discord_control_plane/core/models.py` in the `PRESETS` dict:

```python
PRESETS: dict[str, Preset] = {
    "freedomfighters": Preset("freedomfighters", "values-freedomfighters.yaml", "Freedom Fighters"),
    "proceduralcombat": Preset("proceduralcombat", "values-proceduralcombat.yaml", "Procedural Combat"),
    "yourscenario": Preset("yourscenario", "values-yourscenario.yaml", "Your Scenario Name"),  # ← add this
}
```

### 3. Watch your modset size vs. EBS volume

Each mod gets downloaded to the persistent EBS volume on first launch. The base game is ~10GB, and mods can add several GB more. The volume size is set by `data_volume_size` in `vars.tf` (default: 20GB). This same volume also stores Prometheus metrics data and save files.

**If your new scenario has a large modset**, the 20GB default may not be enough. After a launch, check disk usage via SSH (`df -h /opt/arma-server-data`). EBS volumes can be grown but never shrunk — if it's getting tight, the aws admin will need to bump `data_volume_size` in the Terraform config.

### 4. Open a PR

Push your branch and open a PR with:
- The new `values-yourscenario.yaml` file
- The updated `models.py` with the preset entry

Once merged to `main`:
- A CI job automatically deploys the updated Lambda code (the bot will accept the new preset name immediately)
- ArgoCD picks up the new values file on the next sync
- The AWS admin will need to re-register the `/launch` slash command with Discord (one-time curl) for the new preset to appear in the autocomplete dropdown

## Known quirks

- **First launch is slow** (~15 min) — SteamCMD downloads ~10GB of game files. Subsequent launches reuse the cached files on the PVC.
- **ArgoCD syncs every 3 minutes** — push a change to `main` branch and wait, or force sync manually.
