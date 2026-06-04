# Reference an existing Route 53 Hosted Zone only if DNS is enabled
data "aws_route53_zone" "primary" {
  count        = var.enable_custom_dns ? 1 : 0
  name         = var.domain_name
  private_zone = false
}

# Create a game-specific A Record only if DNS is enabled
resource "aws_route53_record" "arma_server_dns" {
  count   = var.enable_custom_dns ? 1 : 0

  zone_id = data.aws_route53_zone.primary[0].zone_id
  name    = "arma.${data.aws_route53_zone.primary[0].name}"
  type    = "A"
  ttl     = 300

  records = [
    aws_eip.arma_static_ip.public_ip
  ]
}