#!/usr/bin/env bash
# Ops runbook (already applied once, kept for reference/repeatability on a
# fresh box) for provisioning the client-data-warehouse Postgres setup on the
# staging server. Run as root on the target host, e.g.:
#   ssh -i id_e2estaging root@<host> "bash -s" < staging_warehouse_setup.sh
#
# This is intentionally idempotent-ish but not fully idempotent (CREATE
# DATABASE/ROLE will error if re-run) - it's a record of what was done, not a
# button to press repeatedly.
set -e

echo '=== 1. Provision database + roles (run once) ==='
echo 'Skipping automatic CREATE DATABASE/ROLE here - see docs/client-warehouse.md'
echo 'for the exact one-off SQL used, since it embeds a generated password.'

echo '=== 2. Allow Postgres to listen on all interfaces ==='
sed -i "s/^#listen_addresses = 'localhost'.*/listen_addresses = '*'\t\t# forge: allow remote client-warehouse connections/" \
  /etc/postgresql/16/main/postgresql.conf
grep -n 'listen_addresses' /etc/postgresql/16/main/postgresql.conf

echo '=== 3. pg_hba.conf: allow only the narrow per-client role group, over TLS, to the warehouse db ==='
if ! grep -q 'forge client-warehouse' /etc/postgresql/16/main/pg_hba.conf; then
  cat >> /etc/postgresql/16/main/pg_hba.conf <<'EOF'

# --- forge client-warehouse: added by claude-plugin-poc provisioning ---
# Narrow per-client roles (members of forge_client_group) may connect remotely
# to the forge_warehouse database only, over TLS, with password auth.
# The admin role (forge_admin) is intentionally NOT listed here, so it stays
# reachable only via the existing tunnel-based 127.0.0.1 rule above.
hostssl forge_warehouse    +forge_client_group    0.0.0.0/0    scram-sha-256
hostssl forge_warehouse    +forge_client_group    ::/0         scram-sha-256
# --- end forge client-warehouse ---
EOF
fi
tail -n 10 /etc/postgresql/16/main/pg_hba.conf

echo '=== 4. Defense-in-depth: cap runaway queries at the DB level ==='
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER DATABASE forge_warehouse SET statement_timeout = '30s';"

echo '=== 5. Restart (listen_addresses needs a full restart, not just reload) ==='
systemctl restart postgresql@16-main
sleep 2
systemctl is-active postgresql@16-main
ss -tlnp | grep 5432

echo '=== 6. Sanity: existing app on this box still connects ==='
sudo -u postgres psql -d campaign_manager -c "SELECT 1 AS still_ok;"

cat <<'EOF'

=== MANUAL STEP - NOT DONE BY THIS SCRIPT ===
Port 5432/tcp was verified BLOCKED from the public internet even after this
script ran (OS-level iptables INPUT policy is ACCEPT with no rules, so the
block is at the cloud provider / security-group layer, which is not
SSH-reachable from this box). Someone with access to the hosting provider's
network/firewall console must add an inbound allow rule for tcp/5432 before
any client plugin can reach forge_warehouse directly. Until then, the
warehouse loader and pipeline still work (same box / SSH tunnel), but shipped
plugins cannot connect over the open internet.
EOF
