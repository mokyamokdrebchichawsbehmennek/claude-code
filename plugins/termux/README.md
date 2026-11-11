# Termux Plugin

Connects Claude Code to [Termux](https://termux.dev) on an Android device.
Claude gets a shell on the phone: it can run commands, read and write files,
and call Termux:API helpers (battery, notifications, clipboard, ...).

The plugin ships a small MCP server with no dependencies besides Node.js.

## Tools

| Tool | What it does |
|------|--------------|
| `termux_status` | Checks the connection and reports device, Android and Termux versions, architecture, free storage, and whether Termux:API is installed |
| `termux_exec` | Runs a bash command in Termux (optional `cwd`, `stdin`, `timeout_ms`) and returns stdout, stderr and the exit code |
| `termux_read_file` | Reads a text file from the device |
| `termux_write_file` | Creates, overwrites or appends to a file on the device |

Output is capped at 100 KB per call. Commands are killed after 60 seconds by
default (`timeout_ms`, up to 10 minutes).

## Two ways to connect

### 1. Over ssh (Claude Code on a computer, Termux on the phone)

The computer must be able to reach the phone: same Wi-Fi network, a VPN such
as Tailscale, or USB with `adb forward tcp:8022 tcp:8022` (then use
`TERMUX_HOST=127.0.0.1`).

**On the computer**, make sure you have an ssh key and print the public half:

```bash
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
```

**In Termux**, copy [`scripts/termux-setup.sh`](./scripts/termux-setup.sh) to
the phone and run it with that public key:

```bash
bash termux-setup.sh "ssh-ed25519 AAAA... you@computer"
```

It installs `openssh` if needed, authorizes the key, starts `sshd` on port
8022 and prints the values to use. Run `sshd` again after the phone restarts.

**Back on the computer**, set the variables before starting Claude Code:

```bash
export TERMUX_HOST=192.168.1.20   # the address the script printed
export TERMUX_PORT=8022           # optional, 8022 is the default
export TERMUX_USER=u0_a123        # optional
export TERMUX_SSH_KEY=~/.ssh/id_ed25519   # optional
claude
```

Then ask Claude something like "check the Termux connection".

### 2. Locally (Claude Code running inside Termux)

Leave `TERMUX_HOST` unset. The server detects Termux and runs commands
directly, with no ssh involved.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `TERMUX_HOST` | unset | Phone's hostname or IP address. Setting it selects the ssh transport |
| `TERMUX_PORT` | `8022` | Port of Termux's `sshd` |
| `TERMUX_USER` | ssh's default | ssh user name. Termux's `sshd` accepts any |
| `TERMUX_SSH_KEY` | ssh's defaults | Path to the private key |
| `TERMUX_TIMEOUT_MS` | `60000` | Default command timeout |

## Termux:API

To let Claude use phone features, install the
[Termux:API app](https://wiki.termux.com/wiki/Termux:API) and the package:

```bash
pkg install termux-api
```

Claude can then run, through `termux_exec`, commands such as
`termux-battery-status`, `termux-notification`, `termux-clipboard-get` or
`termux-toast`.

## Security notes

- This plugin gives Claude the same access as your Termux user: everything in
  Termux's home directory and, if you ran `termux-setup-storage`, your shared
  storage. Claude Code's permission prompts apply to each tool call; review
  commands before approving them.
- Authentication is by ssh key only (`BatchMode`); the server never handles a
  password. The first connection records the phone's host key
  (`StrictHostKeyChecking=accept-new`) and later connections fail if it
  changes.
- Do not expose port 8022 to the internet. Keep the phone on a trusted network
  or behind a VPN.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `Termux is not configured` | `TERMUX_HOST` is not set in the environment Claude Code was started from |
| `exit code 255`, "Connection refused" | `sshd` is not running in Termux; run `sshd` |
| `exit code 255`, "Permission denied" | The computer's public key is not in `~/.ssh/authorized_keys` on the phone |
| `exit code 255`, timeout | Wrong address, or the two devices are not on the same network. Android may also suspend Termux: run `termux-wake-lock` |
