#!/usr/bin/env node
// MCP server (stdio, no dependencies) that gives Claude Code a shell on a
// Termux installation.
//
// Two transports, chosen at startup:
//   ssh    TERMUX_HOST is set: every tool call runs over ssh to Termux's sshd.
//   local  TERMUX_HOST is unset and this process itself runs inside Termux.
//
// Configuration (environment variables):
//   TERMUX_HOST      phone's hostname or IP address (enables ssh transport)
//   TERMUX_PORT      sshd port, default 8022 (Termux's default)
//   TERMUX_USER      ssh user; optional, Termux's sshd accepts any name
//   TERMUX_SSH_KEY   path to the private key; default: ssh's own defaults
//   TERMUX_TIMEOUT_MS  default per-command timeout, default 60000

'use strict';

const { spawn } = require('node:child_process');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');

const SERVER_INFO = { name: 'termux', version: '1.0.0' };
const MAX_OUTPUT_BYTES = 100 * 1024;
const MAX_TIMEOUT_MS = 10 * 60 * 1000;
const DEFAULT_TIMEOUT_MS = clampTimeout(process.env.TERMUX_TIMEOUT_MS, 60000);

const HOST = (process.env.TERMUX_HOST || '').trim();
const PORT = (process.env.TERMUX_PORT || '8022').trim();
const USER = (process.env.TERMUX_USER || '').trim();
const KEY = (process.env.TERMUX_SSH_KEY || '').trim();

const IN_TERMUX =
  Boolean(process.env.TERMUX_VERSION) ||
  (process.env.PREFIX || '').includes('com.termux');
const MODE = HOST ? 'ssh' : IN_TERMUX ? 'local' : 'unconfigured';

function clampTimeout(value, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(n, MAX_TIMEOUT_MS);
}

// Quote a string for a POSIX shell.
function q(s) {
  return "'" + String(s).replace(/'/g, "'\\''") + "'";
}

function validateConfig() {
  // These values become ssh arguments, so refuse anything that could be read
  // as an option or carry shell syntax.
  if (HOST && !/^[A-Za-z0-9_][A-Za-z0-9_.:%-]*$/.test(HOST)) {
    return `TERMUX_HOST is not a valid hostname or IP address: ${JSON.stringify(HOST)}`;
  }
  if (!/^\d{1,5}$/.test(PORT)) return `TERMUX_PORT is not a port number: ${JSON.stringify(PORT)}`;
  if (USER && !/^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(USER)) {
    return `TERMUX_USER is not a valid user name: ${JSON.stringify(USER)}`;
  }
  return null;
}

function sshArgs(script) {
  const args = [
    '-p', PORT,
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=10',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'ServerAliveInterval=15',
    // Reuse one connection across tool calls.
    '-o', 'ControlMaster=auto',
    '-o', `ControlPath=${path.join(os.tmpdir(), 'termux-mcp-%C')}`,
    '-o', 'ControlPersist=120',
  ];
  if (KEY) args.push('-i', KEY, '-o', 'IdentitiesOnly=yes');
  args.push('--', USER ? `${USER}@${HOST}` : HOST, script);
  return args;
}

// Run a shell script on Termux. Resolves with { code, signal, stdout, stderr,
// timedOut, truncated }; rejects only when the transport cannot be started.
function run(script, { stdin = null, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    if (MODE === 'unconfigured') {
      return reject(new Error(
        'Termux is not configured. Set TERMUX_HOST to the phone\'s address ' +
        '(and TERMUX_PORT, TERMUX_USER, TERMUX_SSH_KEY if needed), or run ' +
        'Claude Code inside Termux. See the plugin README.'));
    }
    const configError = validateConfig();
    if (configError) return reject(new Error(configError));

    const child = MODE === 'ssh'
      ? spawn('ssh', sshArgs(script), { stdio: ['pipe', 'pipe', 'pipe'] })
      : spawn('bash', ['-c', script], { stdio: ['pipe', 'pipe', 'pipe'] });

    const out = { stdout: [], stderr: [] };
    let size = 0;
    let truncated = false;
    let timedOut = false;
    const collect = (name) => (chunk) => {
      const room = MAX_OUTPUT_BYTES - size;
      if (room <= 0) { truncated = true; return; }
      if (chunk.length > room) { chunk = chunk.subarray(0, room); truncated = true; }
      size += chunk.length;
      out[name].push(chunk);
    };
    child.stdout.on('data', collect('stdout'));
    child.stderr.on('data', collect('stderr'));

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, timeoutMs);

    child.on('error', (err) => {
      clearTimeout(timer);
      reject(err.code === 'ENOENT'
        ? new Error(`${MODE === 'ssh' ? 'ssh' : 'bash'} is not installed on this machine`)
        : err);
    });
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      resolve({
        code, signal, timedOut, truncated,
        stdout: Buffer.concat(out.stdout).toString('utf8'),
        stderr: Buffer.concat(out.stderr).toString('utf8'),
      });
    });

    child.stdin.on('error', () => {}); // remote side may exit before reading
    child.stdin.end(stdin === null ? undefined : stdin);
  });
}

function describe(result) {
  const parts = [];
  if (result.stdout) parts.push(result.stdout.replace(/\n$/, ''));
  if (result.stderr) parts.push(`[stderr]\n${result.stderr.replace(/\n$/, '')}`);
  if (result.truncated) parts.push(`[output truncated at ${MAX_OUTPUT_BYTES} bytes]`);
  if (result.timedOut) parts.push('[command timed out and was killed]');
  else if (result.code !== 0) {
    parts.push(result.code === null
      ? `[killed by signal ${result.signal}]`
      : `[exit code ${result.code}]`);
    if (MODE === 'ssh' && result.code === 255) {
      parts.push('[exit code 255 usually means ssh itself failed: check that ' +
        'sshd is running in Termux, the address and port are right, and the ' +
        'key is authorized]');
    }
  }
  return parts.join('\n') || '[no output]';
}

const failed = (r) => r.timedOut || r.code !== 0;

const TOOLS = [
  {
    name: 'termux_exec',
    description:
      'Run a bash command in Termux on the user\'s Android device and return ' +
      'its stdout, stderr and exit code. Termux:API commands (termux-battery-status, ' +
      'termux-notification, termux-clipboard-get, ...) work here when the ' +
      'termux-api package is installed.',
    inputSchema: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'Bash command line to run.' },
        cwd: { type: 'string', description: 'Working directory. Defaults to the Termux home directory.' },
        stdin: { type: 'string', description: 'Text to send to the command\'s standard input.' },
        timeout_ms: { type: 'number', description: `Kill the command after this many milliseconds (default ${DEFAULT_TIMEOUT_MS}, max ${MAX_TIMEOUT_MS}).` },
      },
      required: ['command'],
    },
    async handler({ command, cwd, stdin, timeout_ms }) {
      if (typeof command !== 'string' || !command.trim()) throw new Error('command is required');
      const script = `cd ${cwd ? q(cwd) : '"$HOME"'} && exec bash -c ${q(command)}`;
      const result = await run(script, {
        stdin: typeof stdin === 'string' ? stdin : null,
        timeoutMs: clampTimeout(timeout_ms, DEFAULT_TIMEOUT_MS),
      });
      return { text: describe(result), isError: failed(result) };
    },
  },
  {
    name: 'termux_read_file',
    description: `Read a text file from the Termux device (first ${MAX_OUTPUT_BYTES} bytes).`,
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'File path; relative paths resolve against the Termux home directory.' },
      },
      required: ['path'],
    },
    async handler({ path: file }) {
      if (typeof file !== 'string' || !file) throw new Error('path is required');
      const result = await run(`cd "$HOME" && exec cat -- ${q(file)}`);
      return { text: describe(result), isError: failed(result) };
    },
  },
  {
    name: 'termux_write_file',
    description: 'Create or overwrite a text file on the Termux device, creating parent directories as needed.',
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'File path; relative paths resolve against the Termux home directory.' },
        content: { type: 'string', description: 'Full new content of the file.' },
        append: { type: 'boolean', description: 'Append to the file instead of replacing it.' },
      },
      required: ['path', 'content'],
    },
    async handler({ path: file, content, append }) {
      if (typeof file !== 'string' || !file) throw new Error('path is required');
      if (typeof content !== 'string') throw new Error('content is required');
      const script =
        `cd "$HOME" && mkdir -p -- "$(dirname -- ${q(file)})" && ` +
        `cat ${append ? '>>' : '>'} ${q(file)}`;
      const result = await run(script, { stdin: content });
      if (failed(result)) return { text: describe(result), isError: true };
      return { text: `Wrote ${Buffer.byteLength(content)} bytes to ${file}`, isError: false };
    },
  },
  {
    name: 'termux_status',
    description:
      'Check the connection to Termux and report the device, Android and ' +
      'Termux versions, architecture, home directory, free storage and ' +
      'whether Termux:API is installed. Call this first when unsure whether ' +
      'the device is reachable.',
    inputSchema: { type: 'object', properties: {} },
    async handler() {
      const script = [
        'echo "termux_version: ${TERMUX_VERSION:-unknown}"',
        'echo "android: $(getprop ro.build.version.release 2>/dev/null || echo unknown)"',
        'echo "device: $(getprop ro.product.manufacturer 2>/dev/null) $(getprop ro.product.model 2>/dev/null)"',
        'echo "arch: $(uname -m)"',
        'echo "user: $(id -un 2>/dev/null || whoami)"',
        'echo "home: $HOME"',
        'echo "termux_api: $(command -v termux-battery-status >/dev/null 2>&1 && echo installed || echo "not installed (pkg install termux-api)")"',
        'echo "storage_free: $(df -h "$HOME" 2>/dev/null | awk \'NR==2 {print $4}\')"',
      ].join('; ');
      const result = await run(script, { timeoutMs: 20000 });
      const header = MODE === 'ssh'
        ? `transport: ssh ${USER ? USER + '@' : ''}${HOST}:${PORT}`
        : 'transport: local (running inside Termux)';
      return { text: `${header}\n${describe(result)}`, isError: failed(result) };
    },
  },
];

function send(message) {
  process.stdout.write(JSON.stringify(message) + '\n');
}

async function handle(msg) {
  const { id, method, params } = msg;
  const isRequest = id !== undefined && id !== null;
  const reply = (result) => isRequest && send({ jsonrpc: '2.0', id, result });
  const fail = (code, message) => isRequest && send({ jsonrpc: '2.0', id, error: { code, message } });

  switch (method) {
    case 'initialize':
      return reply({
        protocolVersion: (params && params.protocolVersion) || '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: SERVER_INFO,
        instructions:
          'Tools for the user\'s Android device through Termux. ' +
          (MODE === 'unconfigured'
            ? 'Not configured yet: TERMUX_HOST is unset.'
            : `Transport: ${MODE}.`),
      });
    case 'ping':
      return reply({});
    case 'tools/list':
      return reply({
        tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })),
      });
    case 'tools/call': {
      const tool = TOOLS.find((t) => t.name === (params && params.name));
      if (!tool) return fail(-32602, `Unknown tool: ${params && params.name}`);
      try {
        const { text, isError } = await tool.handler((params && params.arguments) || {});
        return reply({ content: [{ type: 'text', text }], isError });
      } catch (err) {
        return reply({ content: [{ type: 'text', text: `Error: ${err.message}` }], isError: true });
      }
    }
    default:
      if (isRequest) fail(-32601, `Method not found: ${method}`);
  }
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
const inFlight = new Set();
rl.on('line', (line) => {
  if (!line.trim()) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
  }
  // Calls run concurrently; each one is tracked so that a closing client
  // still gets the answers to the requests it already sent.
  const task = handle(msg)
    .catch((err) => process.stderr.write(`termux-mcp: ${err.stack || err}\n`))
    .finally(() => inFlight.delete(task));
  inFlight.add(task);
});
rl.on('close', () => Promise.all(inFlight).then(() => process.exit(0)));
