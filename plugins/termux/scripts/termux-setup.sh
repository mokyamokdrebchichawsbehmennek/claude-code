#!/data/data/com.termux/files/usr/bin/bash
# Run this INSIDE Termux, once, to let Claude Code connect over ssh.
#
#   bash termux-setup.sh "ssh-ed25519 AAAA... you@computer"
#
# The argument is the PUBLIC key of the computer that runs Claude Code
# (the content of ~/.ssh/id_ed25519.pub). Without an argument the script only
# installs and starts sshd and prints the connection details.

set -euo pipefail

if [ -z "${PREFIX:-}" ] || [[ "$PREFIX" != *com.termux* ]]; then
  echo "This script must run inside Termux." >&2
  exit 1
fi

if ! command -v sshd >/dev/null 2>&1; then
  pkg install -y openssh
fi

if [ $# -ge 1 ]; then
  key="$1"
  case "$key" in
    ssh-*|ecdsa-*|sk-*) ;;
    *) echo "That does not look like an ssh public key." >&2; exit 1 ;;
  esac
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  touch "$HOME/.ssh/authorized_keys"
  chmod 600 "$HOME/.ssh/authorized_keys"
  if ! grep -qxF -- "$key" "$HOME/.ssh/authorized_keys"; then
    printf '%s\n' "$key" >> "$HOME/.ssh/authorized_keys"
    echo "Public key added to ~/.ssh/authorized_keys"
  else
    echo "Public key already authorized"
  fi
fi

pgrep -x sshd >/dev/null 2>&1 || sshd

# Keep Termux alive while the screen is off, when the helper is available.
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

ip_addr="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"

echo
echo "sshd is running. On the computer that runs Claude Code, set:"
echo "  export TERMUX_HOST=${ip_addr:-<phone ip address>}"
echo "  export TERMUX_PORT=8022"
echo "  export TERMUX_USER=$(whoami)"
