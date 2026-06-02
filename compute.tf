# Variable to toggle the compute footprint on or off
variable "instance_count" {
  type        = number
  default     = 0
  description = "Set to 1 to boot the game server, set to 0 to tear it down"
}

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
    volume_size           = 50
    iops                  = 3000
    throughput            = 125
    delete_on_termination = false # CRITICAL: Prevents data loss when instance_count = 0
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

              mkdir -p /opt/arma-server-data
              chown root:root /opt/arma-server-data

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