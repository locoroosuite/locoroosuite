# Mirror of production /var/lib/dovecot/sieve/global-spam.sieve:
# file delivery-time spam (X-Spam header added by the rspamd milter) into Junk.
require "fileinto";

if header :is "X-Spam" "yes" {
    fileinto "Junk";
    stop;
}
