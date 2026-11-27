#!/bin/sh
# Tell the bebop-uptime Worker that the VM is alive. Run from cron every 5 minutes.
# The secret stays off the command line and out of the URL: curl reads the
# Authorization header from stdin (-K -).
secret_file="$HOME/.config/bebop-uptime/secret"
[ -r "$secret_file" ] || { echo "bebop-uptime-ping: no $secret_file" >&2; exit 1; }
secret=$(tr -d '\n' < "$secret_file")
case "$secret" in *[!0-9a-f]*|"") bad=1 ;; *) [ ${#secret} -eq 64 ] && bad=0 || bad=1 ;; esac
[ "$bad" = 0 ] || { echo "bebop-uptime-ping: $secret_file is not 64 lowercase hex characters" >&2; exit 1; }
printf 'header = "Authorization: Bearer %s"\n' "$secret" \
  | curl -fsS -m 15 --retry 2 --retry-delay 5 -o /dev/null -X POST -K - \
      https://bebop-uptime.rex-651.workers.dev/ping/bebop-vm
