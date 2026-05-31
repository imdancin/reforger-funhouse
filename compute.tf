variable "active_scenario_config" {
  type        = string
  default     = "values-freedomfighters.yaml"
  description = "The target scenario values file name to load inside the cluster-manifests directory"
}

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

              # 1. Update system packages and install Amazon SSM Agent
              apt-get update -y
              apt-get upgrade -y
              apt-get install -y curl
              apt-get install -y amazon-ssm-agent || {
                curl -fsSL "https://s3.us-west-2.amazonaws.com/amazon-ssm-us-west-2/latest/debian_amd64/amazon-ssm-agent.deb" -o /tmp/amazon-ssm-agent.deb
                dpkg -i /tmp/amazon-ssm-agent.deb
              }
              systemctl enable amazon-ssm-agent
              systemctl start amazon-ssm-agent

              # 2. Optimize Kernel parameters for intensive UDP game traffic
              cat <<-SYS | tee -a /etc/sysctl.conf
              net.core.rmem_max=16777216
              net.core.wmem_max=16777216
              net.core.rmem_default=16777216
              net.core.wmem_default=16777216
              SYS
              sysctl -p

              # 3. FIXED: Complete untruncated IMDSv2 endpoint mapping blocks
              AWS_TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
              LOCAL_IP=$(curl -s -H "X-aws-ec2-metadata-token: $AWS_TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

              mkdir -p /opt/arma-server-data
              chown root:root /opt/arma-server-data

              # 4. FIXED: Complete tracking endpoint to target the real K3s installation script
              curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
                --disable traefik \
                --disable local-storage \
                --node-ip=$LOCAL_IP \
                --flannel-backend=host-gw" sh -

              # 5. Wait for Cluster Node to report Ready status
              echo "Waiting for K3s node to come online..."
              until /usr/local/bin/kubectl get node | grep -q "Ready"; do
                sleep 5
              done

              # 6. FIXED: Restored complete raw github pathways for the ArgoCD control engine
              echo "Installing ArgoCD..."
              /usr/local/bin/kubectl create namespace argocd || true
              /usr/local/bin/kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

              # 7. Wait for ArgoCD API Server to be fully operational
              echo "Waiting for ArgoCD deployments to stabilize..."
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=5m
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-application-controller -n argocd --timeout=5m
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-repo-server -n argocd --timeout=5m
              /usr/local/bin/kubectl wait --for=condition=Available deployment/argocd-dex-server -n argocd --timeout=5m || true

              # 8. Wait for the ArgoCD Application CRD to become available
              echo "Waiting for ArgoCD CRDs to register..."
              until /usr/local/bin/kubectl get crd applications.argoproj.io >/dev/null 2>&1; do
                sleep 5
              done
              /usr/local/bin/kubectl wait --for=condition=established crd/applications.argoproj.io --timeout=5m || true

              # 9. Fully hydrate your explicit Git repository and API endpoint destinations
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
                      - ${var.active_scenario_config}
                destination:
                  server: 'https://kubernetes.default.svc'
                  namespace: default
                syncPolicy:
                  automated:
                    prune: true
                    selfHeal: true
              MANIFEST

              echo "=== K3s, ArgoCD, and Arma Reforger Bootstrap Complete ==="
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