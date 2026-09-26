require ["vnd.dovecot.pipe", "copy", "imapsieve"];

# Fires on COPY out of the Junk folder (imapsieve_mailbox2_*); the trigger
# is already scoped to source=Junk by the Dovecot config, so the message
# on stdin is always ham.
pipe "learn-ham.sh";
