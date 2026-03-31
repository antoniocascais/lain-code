#!/bin/sh
# Copy mounted credentials to where the SDK expects them ($HOME/.claude/).
# /tmp is always writable regardless of UID override in compose.
mkdir -p "$HOME/.claude"
cp /credentials/.credentials.json "$HOME/.claude/.credentials.json" 2>/dev/null || true

# Restrict egress to Anthropic API endpoints.
# Needs cap_add: NET_ADMIN in compose + file capabilities on iptables binary.
if iptables -L OUTPUT -n >/dev/null 2>&1; then
  ALLOWED_HOSTS="${LAIN_ALLOWED_HOSTS:-api.anthropic.com}"

  iptables -A OUTPUT -o lo -j ACCEPT
  iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  # DNS — needed to resolve allowed hosts at runtime
  iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
  iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

  host_rules=0
  for host in $ALLOWED_HOSTS; do
    for ip in $(getent ahosts "$host" | awk '{print $1}' | sort -u); do
      iptables -A OUTPUT -d "$ip" -p tcp --dport 443 -j ACCEPT
      host_rules=$((host_rules + 1))
    done
  done

  if [ "$host_rules" -gt 0 ]; then
    iptables -A OUTPUT -j DROP
    echo "egress: locked to $ALLOWED_HOSTS ($host_rules IPs)"
  else
    echo "warning: DNS resolution failed, skipping egress filter"
  fi
else
  echo "warning: iptables unavailable (no NET_ADMIN cap), egress unrestricted"
fi

exec "$@"
