resource "aws_instance" "app_server" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t2.micro"

  tags = {
    Name = "PolyglotAppServer"
    Role = "worker"
  }
}

variable "app_port" {
  description = "Port for the application to run"
  default     = 8080
}
