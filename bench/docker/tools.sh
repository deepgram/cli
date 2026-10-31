#!/bin/sh
# Shared image setup for every bench image (runs as root at build time).
# Installs the measurement tools (strace, GNU time, python3 when missing) and a
# sha256-verified hyperfine. Args: HYPERFINE_VERSION SHA_ARM64 SHA_AMD64
set -eu
HV=$1; SHA_ARM64=$2; SHA_AMD64=$3
PKGS="strace time procps ca-certificates curl"
command -v python3 >/dev/null 2>&1 || PKGS="$PKGS python3"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $PKGS >/dev/null
rm -rf /var/lib/apt/lists/*
ARCH=$(dpkg --print-architecture)
case $ARCH in
  arm64) T=aarch64-unknown-linux-gnu; SHA=$SHA_ARM64 ;;
  amd64) T=x86_64-unknown-linux-gnu; SHA=$SHA_AMD64 ;;
  *) echo "unsupported arch $ARCH" >&2; exit 1 ;;
esac
curl -fsSL -o /tmp/hf.tgz "https://github.com/sharkdp/hyperfine/releases/download/v$HV/hyperfine-v$HV-$T.tar.gz"
echo "$SHA  /tmp/hf.tgz" | sha256sum -c - >/dev/null
tar xzf /tmp/hf.tgz -C /tmp
install -m 0755 "/tmp/hyperfine-v$HV-$T/hyperfine" /usr/local/bin/hyperfine
rm -rf /tmp/hf.tgz "/tmp/hyperfine-v$HV-$T"
# Compile every interpreter's stdlib to .pyc so no bench row pays for source
# compilation (the official python images ship without stdlib .pyc).
for py in /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$py" ] || continue
  "$py" -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])' | while read -r d; do
    "$py" -m compileall -q -j0 "$d" >/dev/null 2>&1 || true
  done
done
mkdir -p /opt/bench
strace -V | head -1 > /opt/bench/strace.txt
useradd -m u
