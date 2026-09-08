#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
ca_dir="${FORK_CA_DIR:-env/proxy}"
umask 077
mkdir -p "$ca_dir"
if [ -s "$ca_dir/mitmproxy-ca.pem" ]; then
  openssl x509 -in "$ca_dir/mitmproxy-ca.pem" -noout >/dev/null
  openssl x509 -in "$ca_dir/mitmproxy-ca.pem" -out "$ca_dir/mitmproxy-ca-cert.pem"
  echo "Using existing local proxy CA."
  exit 0
fi
if [ -e "$ca_dir/mitmproxy-ca-cert.pem" ]; then
  echo "A CA certificate exists without its private key. Restore the matching key or choose a fresh FORK_CA_DIR." >&2
  exit 1
fi
ca_tmp=$(mktemp -d "$ca_dir/.generate.XXXXXX")
trap 'rm -rf "$ca_tmp"' EXIT
cat > "$ca_tmp/openssl.cnf" <<'CONFIG'
[req]
distinguished_name = subject
x509_extensions = ca
prompt = no
[subject]
CN = Fork Local Proxy CA
O = Fork
[ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
CONFIG
openssl req -new -x509 -newkey rsa:2048 -nodes -days 3650 \
  -config "$ca_tmp/openssl.cnf" -keyout "$ca_tmp/key.pem" -out "$ca_tmp/cert.pem" 2>/dev/null
cat "$ca_tmp/key.pem" "$ca_tmp/cert.pem" > "$ca_dir/mitmproxy-ca.pem"
cp "$ca_tmp/cert.pem" "$ca_dir/mitmproxy-ca-cert.pem"
echo "Generated a local proxy CA; private material stays outside Git and Docker builds."
