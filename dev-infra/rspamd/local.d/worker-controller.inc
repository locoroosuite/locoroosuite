# Dev: controller must accept learn requests from the Dovecot container
# (IMAPSieve pipe scripts via curl) and from the host (e2e checks on
# 127.0.0.1:11334). Passwordless access is allowed for local + private
# docker ranges only; production instead uses localhost-only secure_ip.
count = 1;
bind_socket = "0.0.0.0:11334";
secure_ip = "127.0.0.1";
secure_ip = "::1";
secure_ip = "10.0.0.0/8";
secure_ip = "172.16.0.0/12";
static_dir = "${WWWDIR}";
