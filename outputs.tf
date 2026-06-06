output "arma_instance_id" {
  description = "The ID of the active EC2 compute instance running the game cluster"
  # Using the splat syntax and try() handles the count array gracefully when set to 0
  value       = try(aws_instance.arma_server[0].id, "")
}

output "arma_server_public_ip" {
  description = "The permanent static IP address players will use to connect to the server"
  value       = aws_eip.arma_static_ip.public_ip
}

output "game_data_volume_id" {
  description = "The EBS volume ID for persistent game data (survives instance teardown)"
  value       = aws_ebs_volume.game_data.id
}
