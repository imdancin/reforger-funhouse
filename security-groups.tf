resource "aws_security_group" "arma_server_sg" {
  name        = "arma-reforger-server-sg"
  description = "Network boundaries for Arma Reforger dedicated game server"
  vpc_id      = aws_vpc.game_vpc.id

  # Arma Reforger Game Simulation Traffic (Default Engine Port)
  ingress {
    description = "Arma Reforger Game Port"
    from_port   = 2001
    to_port     = 2001
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Steam Master Server Query Protocol
  ingress {
    description = "Steam Query Port"
    from_port   = 1999
    to_port     = 1999
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Stateful Outbound Traffic Engine
  egress {
    description = "Allow all outbound traffic for system updates and SteamCMD patches"
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # Specifies all protocols natively
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "arma-server-security-group"
  }
}


resource "aws_security_group_rule" "ssh_ingress" {
  count = var.ssh_allowed_cidr != "" ? 1 : 0

  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [var.ssh_allowed_cidr]
  description       = "SSH access from operator home IP"
  security_group_id = aws_security_group.arma_server_sg.id
}
