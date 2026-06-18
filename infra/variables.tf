variable "prefix" { default = "fieldops" }
variable "location" { default = "eastus" }
variable "vm_size" { default = "Standard_B2s" }
variable "admin_username" { default = "azureuser" }
variable "ssh_public_key" { type = string }     # contents of your .pub
variable "allowed_ip" { type = string }         # your IP/32 for SSH+HTTP
variable "dt_environment_url" { type = string } # https://<env>.sprint.dynatracelabs.com
variable "dt_paas_token" {
  type      = string
  sensitive = true
}
