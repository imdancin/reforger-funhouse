param(
    [string]$KeyPath = "$HOME/.ssh/arma_reforger_ed25519"
)

# Resolve private key
$resolvedKey = Resolve-Path $KeyPath -ErrorAction SilentlyContinue
if (-not $resolvedKey) {
    Write-Error "Private key not found at: $KeyPath"
    exit 1
}

# Get Elastic IP from Terraform state
$ip = terraform output -raw arma_server_public_ip 2>$null
if (-not $ip) {
    Write-Error "Could not resolve server IP. Is the instance deployed? Is Terraform state accessible?"
    exit 1
}

# Connect with 10-second timeout
ssh -i $resolvedKey -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ubuntu@$ip
if ($LASTEXITCODE -ne 0) {
    Write-Error "SSH connection failed. Possible causes: instance not running, key mismatch, or security group not configured."
    exit $LASTEXITCODE
}
