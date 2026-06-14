# Persistent Elastic IP allocated permanently
resource "aws_eip" "arma_static_ip" {
  domain = "vpc"

  tags = {
    Name = "arma-static-ip"
  }
}

# Conditional EC2 Compute Instance
resource "aws_instance" "arma_server" {
  count = var.instance_count

  ami                    = "ami-0606dd43116f5ed57"   # Official Canonical Ubuntu 24.04 LTS x86_64 AMI
  instance_type        = "c6i.xlarge"
  subnet_id            = aws_subnet.public_subnet.id
  vpc_security_group_ids = [aws_security_group.arma_server_sg.id]
  
  # Structural lifecycles for graceful teardowns
  instance_initiated_shutdown_behavior = "stop"
  
  # IAM Instance profile for AWS Systems Manager authentication
  iam_instance_profile = aws_iam_instance_profile.ssm_profile.name

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    iops                  = 3000
    throughput            = 125
    delete_on_termination = true # Safe now — persistent data lives on the dedicated EBS volume
  }

  user_data = <<-EOF
              #!/bin/bash
              set -euo pipefail

              echo "=== Starting Game Server Node Bootstrap ==="

              # 0. Set a stable hostname for the K3s node and local PV affinity
              hostnamectl set-hostname arma-reforger-compute
              echo "arma-reforger-compute" > /etc/hostname

              # 1. Update system packages and install dependencies
              apt-get update -y
              apt-get upgrade -y
              apt-get install -y curl unzip

              # --- SSH Public Key Injection (conditional) ---
              SSH_PUBLIC_KEY="${var.ssh_public_key}"
              if [ -n "$SSH_PUBLIC_KEY" ]; then
                mkdir -p /home/ubuntu/.ssh
                echo "$SSH_PUBLIC_KEY" > /home/ubuntu/.ssh/authorized_keys
                chown -R ubuntu:ubuntu /home/ubuntu/.ssh
                chmod 700 /home/ubuntu/.ssh
                chmod 600 /home/ubuntu/.ssh/authorized_keys

                # Harden sshd configuration
                cat > /etc/ssh/sshd_config.d/99-arma-hardening.conf <<SSHD
              PubkeyAuthentication yes
              PasswordAuthentication no
              SSHD
                systemctl restart sshd
              fi

              # 2. Install AWS CLI v2 (required for SSM parameter reads/writes)
              curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
              unzip -q /tmp/awscliv2.zip -d /tmp
              /tmp/aws/install
              export PATH=$PATH:/usr/local/bin
              rm -rf /tmp/awscliv2.zip /tmp/aws

              # NOW safe to set the ERR trap — aws CLI is available
              trap 'aws ssm put-parameter --name /arma-reforger/bootstrap-status --value "failed:$(date -u +%Y-%m-%dT%H:%M:%SZ)" --type String --overwrite' ERR

              # 3. Install Amazon SSM Agent (snap version already present on Ubuntu 24.04 — skip deb)
              systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service || true
              systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service || true

              # 4. Optimize Kernel parameters for intensive UDP game traffic
              cat <<-SYS | tee -a /etc/sysctl.conf
              net.core.rmem_max=16777216
              net.core.wmem_max=16777216
              net.core.rmem_default=16777216
              net.core.wmem_default=16777216
              SYS
              sysctl -p

              # 5. Get instance local IP via IMDSv2
              AWS_TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
              LOCAL_IP=$(curl -s -H "X-aws-ec2-metadata-token: $AWS_TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

              # --- Mount dedicated EBS data volume at /opt/arma-server-data ---
              DATA_MOUNT="/opt/arma-server-data"

              # Resolve the data volume device — Nitro instances use /dev/nvme*
              # The Terraform-attached volume appears as /dev/xvdf in the API but
              # maps to an NVMe device on c6i/c5/m5/etc. Use nvme id-ctrl to find it.
              echo "Locating data volume device..."
              DATA_DEVICE=""
              WAIT_START=$(date +%s)
              while [ -z "$DATA_DEVICE" ]; do
                # Look for NVMe devices that map to the xvdf attachment
                for dev in /dev/nvme*n1; do
                  [ -b "$dev" ] || continue
                  # Skip the root device
                  if lsblk "$dev" | grep -q '/$'; then
                    continue
                  fi
                  # Skip devices with partition tables (legacy root volumes)
                  if lsblk -n "$dev" | grep -q 'part'; then
                    continue
                  fi
                  DATA_DEVICE="$dev"
                  break
                done
                if [ -z "$DATA_DEVICE" ]; then
                  sleep 2
                  ELAPSED=$(( $(date +%s) - WAIT_START ))
                  if [ "$ELAPSED" -gt 120 ]; then
                    echo "ERROR: Data volume device not found within 120s"
                    exit 1
                  fi
                fi
              done
              echo "Data volume detected at $DATA_DEVICE"

              # Format only if no filesystem exists (first-time setup)
              if ! blkid "$DATA_DEVICE" | grep -q 'TYPE='; then
                echo "No filesystem found on $DATA_DEVICE — formatting as ext4..."
                mkfs.ext4 -L arma-data "$DATA_DEVICE"
              fi

              # If data already exists on root disk, preserve it for migration
              MIGRATION_NEEDED=false
              if [ -d "$DATA_MOUNT" ] && [ "$(ls -A $DATA_MOUNT 2>/dev/null)" ]; then
                echo "Existing data found at $DATA_MOUNT — will migrate to new volume."
                MIGRATION_NEEDED=true
                mv "$DATA_MOUNT" /tmp/arma-server-data-migration
              fi

              # Mount the dedicated volume
              mkdir -p "$DATA_MOUNT"
              mount "$DATA_DEVICE" "$DATA_MOUNT"

              # Add to fstab for persistence across reboots (idempotent)
              if ! grep -q "$DATA_DEVICE" /etc/fstab; then
                echo "$DATA_DEVICE $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
              fi

              # Migrate data from root volume to new EBS volume if needed
              if [ "$MIGRATION_NEEDED" = true ]; then
                echo "Migrating data to new volume..."
                rsync -a /tmp/arma-server-data-migration/ "$DATA_MOUNT/"

                # Verify migration — compare file counts and total size
                SRC_COUNT=$(find /tmp/arma-server-data-migration -type f | wc -l)
                DST_COUNT=$(find "$DATA_MOUNT" -type f | wc -l)
                SRC_SIZE=$(du -sb /tmp/arma-server-data-migration | awk '{print $1}')
                DST_SIZE=$(du -sb "$DATA_MOUNT" | awk '{print $1}')

                echo "Migration verification:"
                echo "  Source files: $SRC_COUNT | Destination files: $DST_COUNT"
                echo "  Source size:  $SRC_SIZE bytes | Destination size: $DST_SIZE bytes"

                if [ "$SRC_COUNT" -eq "$DST_COUNT" ] && [ "$SRC_SIZE" -eq "$DST_SIZE" ]; then
                  echo "VERIFIED: All files migrated successfully."
                  rm -rf /tmp/arma-server-data-migration
                else
                  echo "WARNING: Migration verification mismatch! Keeping source at /tmp/arma-server-data-migration for manual inspection."
                fi
              fi

              # Ensure subdirectories exist with correct permissions
              mkdir -p "$DATA_MOUNT/grafana"
              mkdir -p "$DATA_MOUNT/prometheus"
              chown root:root "$DATA_MOUNT"
              chmod 777 "$DATA_MOUNT/grafana"
              chmod 777 "$DATA_MOUNT/prometheus"

              # 6. Install Helm
              echo "Installing Helm..."
              curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

              # 7. Install K3s
              curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
                --disable traefik \
                --disable local-storage \
                --node-ip=$LOCAL_IP \
                --flannel-backend=host-gw" sh -

              # 8. Wait for Cluster Node to report Ready status
              echo "Waiting for K3s node to come online..."
              until /usr/local/bin/kubectl get node | grep -q "Ready"; do
                sleep 5
              done

              # 9a. Install External Secrets Operator via Helm
              echo "Installing External Secrets Operator..."
              export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
              helm repo add external-secrets https://charts.external-secrets.io
              helm install external-secrets external-secrets/external-secrets \
                -n external-secrets --create-namespace \
                --set installCRDs=true
              /usr/local/bin/kubectl wait --for=condition=Available deployment/external-secrets \
                -n external-secrets --timeout=5m

              # 9b. Retrieve ESO IAM credentials from SSM and create the eso-aws-credentials Kubernetes Secret
              echo "Creating eso-aws-credentials Kubernetes Secret..."
              ESO_KEY_ID=$(aws ssm get-parameter --name /arma-reforger/eso-access-key-id --query Parameter.Value --output text)
              ESO_SECRET=$(aws ssm get-parameter --name /arma-reforger/eso-secret-access-key --with-decryption --query Parameter.Value --output text)
              /usr/local/bin/kubectl create secret generic eso-aws-credentials \
                --from-literal=access-key-id="$ESO_KEY_ID" \
                --from-literal=secret-access-key="$ESO_SECRET" \
                -n default

              # 10. Install ArgoCD
              echo "Installing ArgoCD..."
              /usr/local/bin/kubectl create namespace argocd || true
              /usr/local/bin/kubectl apply -n argocd --server-side -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

              # 11. Wait for ArgoCD to be fully operational
              echo "Waiting for ArgoCD deployments to stabilize..."
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=5m
              echo "Waiting for argocd-application-controller StatefulSet to roll out..."
              /usr/local/bin/kubectl rollout status statefulset/argocd-application-controller -n argocd --timeout=5m
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-repo-server -n argocd --timeout=5m
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-dex-server -n argocd --timeout=5m || true

              # 12. Wait for ArgoCD Application CRD
              echo "Waiting for ArgoCD CRDs to register..."
              until /usr/local/bin/kubectl get crd applications.argoproj.io >/dev/null 2>&1; do
                sleep 5
              done
              /usr/local/bin/kubectl wait --for=condition=established crd/applications.argoproj.io --timeout=5m || true

              # 13. Apply ArgoCD Application manifest
              echo "Hydrating cluster state via GitOps..."
              /usr/local/bin/kubectl apply -f - <<MANIFEST
              apiVersion: argoproj.io/v1alpha1
              kind: Application
              metadata:
                name: root-arma-app
                namespace: argocd
                annotations:
                  notifications.argoproj.io/subscribe.on-deployed.slack: "arma-game-alerts"
              spec:
                project: default
                source:
                  repoURL: 'https://github.com/imdancin/reforger-funhouse.git'
                  targetRevision: main
                  path: cluster-manifests
                  helm:
                    valueFiles:
                      - $(aws ssm get-parameter --name /arma-reforger/active-scenario --query Parameter.Value --output text)
                destination:
                  server: 'https://kubernetes.default.svc'
                  namespace: default
                syncPolicy:
                  automated:
                    prune: true
                    selfHeal: true
              MANIFEST

              echo "=== K3s, ArgoCD, and Arma Reforger Bootstrap Complete ==="
              aws ssm put-parameter --name /arma-reforger/bootstrap-status --value "ready:$(date -u +%Y-%m-%dT%H:%M:%SZ)" --type String --overwrite
              EOF

  tags = {
    Name = "arma-reforger-compute"
  }

  lifecycle {
    ignore_changes = [user_data]
  }
}

# Persistent game data volume — survives instance teardown independently
resource "aws_ebs_volume" "game_data" {
  availability_zone = "us-west-2a"
  size              = var.data_volume_size
  type              = "gp3"
  iops              = 3000
  throughput        = 125

  tags = {
    Name = "arma-game-data"
  }
}

# Attach the data volume to the instance when it exists
resource "aws_volume_attachment" "game_data_attach" {
  count = var.instance_count

  device_name = "/dev/xvdf"
  volume_id   = aws_ebs_volume.game_data.id
  instance_id = aws_instance.arma_server[0].id

  # Prevent Terraform from force-detaching during destroy
  force_detach = false
}

# Temporary attachment of legacy root volume for data recovery (remove after migration)
resource "aws_volume_attachment" "legacy_volume_attach" {
  count = var.legacy_volume_id != "" ? var.instance_count : 0

  device_name = "/dev/xvdg"
  volume_id   = var.legacy_volume_id
  instance_id = aws_instance.arma_server[0].id

  force_detach = false
}

# Dynamic binding linking the static IP to the instance when it exists
resource "aws_eip_association" "eip_assoc" {
  count = var.instance_count

  # FIXED: Added [0] index accessor string to address the counted list array
  instance_id   = aws_instance.arma_server[0].id
  allocation_id = aws_eip.arma_static_ip.id
}

# The IAM Assume Role Policy defining the EC2 machine trust boundary
resource "aws_iam_role" "ssm_role" {
  name = "arma-server-ssm-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

# Attach the standard AWS-managed core policy required for Session Manager
resource "aws_iam_role_policy_attachment" "ssm_attach" {
  role       = aws_iam_role.ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# The physical container profile structure exposed to the virtual machine metadata service
resource "aws_iam_instance_profile" "ssm_profile" {
  name = "arma-server-ssm-instance-profile"
  role = aws_iam_role.ssm_role.name
}