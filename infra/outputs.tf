output "public_ip" { value = azurerm_public_ip.pip.ip_address }
output "url" { value = "http://${azurerm_public_ip.pip.ip_address}" }
