# The foundational Virtual Private Cloud
resource "aws_vpc" "game_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "arma-game-vpc"
  }
}

# Public subnet where the EC2 instance will sit
resource "aws_subnet" "public_subnet" {
  vpc_id                  = aws_vpc.game_vpc.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "us-west-2a" # Fixed AZ ensures EBS volumes can match and attach
  map_public_ip_on_launch = true

  tags = {
    Name = "arma-public-subnet"
  }
}

# Internet Gateway to route inbound and outbound public web traffic
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.game_vpc.id

  tags = {
    Name = "arma-igw"
  }
}

# Route table to direct internet-bound traffic through the IGW
resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.game_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "arma-public-route-table"
  }
}

# Explicitly bind the public subnet to the internet-facing route table
resource "aws_route_table_association" "public_assoc" {
  subnet_id      = aws_subnet.public_subnet.id
  route_table_id = aws_route_table.public_rt.id
}
