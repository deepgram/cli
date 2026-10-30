'use strict';

// Runs the Python Deepgram CLI (deepctl, from PyPI) at the same version as
// this npm package. Nothing here is downloaded at install time: the first
// `dg` either hands off to uv, reuses a cached venv, or builds one.

const childProcess = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const tty = require('node:tty');
const { setTimeout: delay } = require('node:timers/promises');

const PKG_DIR = path.resolve(__dirname, '..');
const MIN_PYTHON = [3, 10];
const LOCK_WAIT_MS = 5 * 60 * 1000;
const LOCK_STALE_MS = 10 * 60 * 1000;
// No first-run install takes this long; past it a same-host lock is
// abandoned even if its pid has been reused by an unrelated process.
const LOCK_MAX_MS = 60 * 60 * 1000;
const LOCK_POLL_MS = 250;
const PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000;
// One-run venvs (Windows) older than this were left by a run that died.
const ONE_RUN_STALE_MS = 60 * 60 * 1000;
const UV_INSTALL_WINDOWS = 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"';
const SIGNAL_GRACE_MS = 1000;
const STDERR_EAGAIN_RETRIES = 100;
const STDERR_EAGAIN_SLEEP_MS = 10;
const PROBE_TIMEOUT_MS = 10 * 1000;
// How long an interrupted setup step gets to exit before it is killed.
const SETUP_KILL_GRACE_MS = 5 * 1000;
const RUNNERS = ['uvx', 'venv', 'path'];

// Prints the version first so a Python without venv/ensurepip (Debian's
// python3 without python3-venv) still reports what it is.
const PROBE_CODE = 'import sys;print(tuple(sys.version_info[:2]));import venv,ensurepip';

// Launcher messages go straight to fd 2. Touching process.stdin, stdout or
// stderr makes Node wrap the fd in a stream, and for a pipe libuv then sets
// O_NONBLOCK on it. deepctl inherits the same open file, and a non-blocking
// stdin breaks `ffmpeg ... | dg listen` and MCP hosts that send their first
// message a moment after starting `dg mcp`.
//
// Writing is best effort: fd 2 may be a full non-blocking pipe (EAGAIN) or
// one whose reader has gone (EPIPE), and a progress line must never abort
// a first-run setup or keep deepctl from running.
function writeStderr(text, opts = {}) {
  const writeSync = opts.writeSync || fs.writeSync;
  const sleep = opts.sleep || sleepSync;
  const buf = Buffer.from(String(text));
  let offset = 0;
  let retries = 0;
  while (offset < buf.length) {
    try {
      offset += writeSync(2, buf, offset, buf.length - offset);
    } catch (err) {
      if (err.code !== 'EAGAIN' || retries++ >= STDERR_EAGAIN_RETRIES) return;
      sleep(STDERR_EAGAIN_SLEEP_MS);
    }
  }
}

const fdStderr = { write: (text) => writeStderr(text) };

class LauncherError extends Error {
  constructor(message, hint = '') {
    super(message);
    this.hint = hint;
  }
}

// --- Setup supervision ----------------------------------------------------
//
// First-run setup (probing Python, waiting for the lock, building the venv)
// runs child processes. A signal that reaches only this process (a
// supervisor's SIGTERM, a closed terminal's SIGHUP) must not leave them
// running, or leave a lock and a half-built venv behind. So setup is async:
// run() installs its signal handlers before anything starts, a session
// tracks the one setup child running at a time, and an interrupt kills it,
// wakes any lock wait, and lets the finally blocks remove the partial venv
// and release the lock before the launcher exits 128+signo.

class Interrupted extends Error {
  constructor(signal) {
    super(`setup interrupted (${signal})`);
    this.signal = signal;
  }
}

function createSession() {
  return { phase: 'setup', child: null, group: false, interrupted: null, abort: new AbortController(), lock: null };
}

function throwIfInterrupted(session) {
  if (session && session.interrupted) throw new Interrupted(session.interrupted);
}

// Setup children run in their own process group on POSIX, so the whole
// tree goes (pip's own subprocesses included) rather than just the parent.
function killSetupChild(session, sig) {
  const { child, group } = session;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (group) process.kill(-child.pid, sig);
    else child.kill(sig);
  } catch {
    try {
      child.kill(sig);
    } catch {
      // Already gone.
    }
  }
}

function interruptSetup(session, sig) {
  if (session.interrupted) {
    // A second signal: stop waiting for a graceful exit.
    killSetupChild(session, 'SIGKILL');
    return;
  }
  session.interrupted = sig;
  session.abort.abort();
  const child = session.child;
  if (!child) return;
  killSetupChild(session, sig);
  const timer = setTimeout(() => {
    if (session.child === child) killSetupChild(session, 'SIGKILL');
  }, SETUP_KILL_GRACE_MS);
  timer.unref();
  child.once('exit', () => clearTimeout(timer));
}

function signalExitCode(sig) {
  const num = os.constants.signals[sig];
  return num ? 128 + num : 1;
}

// Die from `sig` ourselves, so a shell or supervisor sees the signal (a
// bash loop stops on Ctrl-C) rather than a plain exit status. `exitCode`
// (128+signo) is the fallback if the signal does not end us.
function dieBySignal(sig, exitCode) {
  process.removeAllListeners(sig);
  try {
    process.kill(process.pid, sig);
  } catch {
    // Fall through to the numeric exit below.
  }
  setTimeout(() => process.exit(exitCode), 100);
}

// Spawns one setup child under the session and resolves when it is gone:
// {code, signal} on exit, {error} if it could not start.
function superviseChild(command, args, options, session, spawn = childProcess.spawn) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, options);
    } catch (error) {
      resolve({ error });
      return;
    }
    // A real pid only: kill(-0) would signal our own process group.
    const group = Boolean(options.detached) && Number.isInteger(child.pid) && child.pid > 0;
    if (session) Object.assign(session, { child, group });
    // Name the step's process group in the lock, so whoever breaks the lock
    // after a SIGKILLed launcher can stop the pip it left running.
    if (group && session && session.lock) recordSetupGroup(session.lock, child.pid);
    let settled = false;
    const done = (result) => {
      if (settled) return;
      settled = true;
      if (session && session.child === child) {
        // Anything left in the group after an interrupt is a straggler.
        if (group && session.interrupted) {
          try {
            process.kill(-child.pid, 'SIGKILL');
          } catch {
            // Group already empty.
          }
        }
        Object.assign(session, { child: null, group: false });
      }
      resolve({ ...result, child });
    };
    child.once('error', (error) => done({ error }));
    child.once('exit', (code, signal) => done({ code, signal }));
  });
}

function packageVersion(pkgDir = PKG_DIR) {
  return JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8')).version;
}

function pypiSpec(env, version) {
  return env.DEEPCTL_NPM_PYPI_SPEC || `deepctl==${version}`;
}

// Venvs are keyed by version. An overridden spec gets its own directory so
// a CI run against a different deepctl never poisons the real cache entry.
function venvKey(env, version) {
  if (!env.DEEPCTL_NPM_PYPI_SPEC) return version;
  const hash = crypto.createHash('sha256').update(env.DEEPCTL_NPM_PYPI_SPEC).digest('hex');
  return `${version}+${hash.slice(0, 12)}`;
}

// --- PATH resolution ------------------------------------------------------

function isWindows(platform) {
  return platform === 'win32';
}

function exeNames(name, platform) {
  // No PATHEXT and no .cmd/.bat: those need a shell to run, and a shell
  // re-parses the arguments we pass through.
  return isWindows(platform) ? [`${name}.exe`, `${name}.com`] : [name];
}

function pathDirs(env, platform) {
  const raw = (isWindows(platform) ? env.Path || env.PATH : env.PATH) || '';
  const p = isWindows(platform) ? path.win32 : path.posix;
  // Empty and relative entries resolve against the current directory, and
  // Windows searches it implicitly. A launcher run from inside an arbitrary
  // project must not pick up an executable that happens to sit there.
  return raw.split(p.delimiter).filter((dir) => dir && p.isAbsolute(dir));
}

function isExecutableFile(file, platform = process.platform) {
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile()) return false;
    if (isWindows(platform)) return true;
    fs.accessSync(file, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function realpathOrSelf(file) {
  try {
    return fs.realpathSync(file);
  } catch {
    return file;
  }
}

function isInside(child, parent, platform) {
  const p = isWindows(platform) ? path.win32 : path.posix;
  const rel = p.relative(parent, child);
  return rel === '' || (!rel.startsWith('..') && !p.isAbsolute(rel));
}

// Every match for `name` on PATH, in order, minus anything that resolves
// into this package (npm's own `dg` shim must never be mistaken for deepctl).
function whichAll(name, opts = {}) {
  const env = opts.env || process.env;
  const platform = opts.platform || process.platform;
  const isExec = opts.isExecutable || ((f) => isExecutableFile(f, platform));
  const realpath = opts.realpath || realpathOrSelf;
  const pkgDir = realpath(opts.pkgDir || PKG_DIR);
  const join = isWindows(platform) ? path.win32.join : path.posix.join;
  const found = [];
  for (const dir of pathDirs(env, platform)) {
    for (const exe of exeNames(name, platform)) {
      const candidate = join(dir, exe);
      if (!isExec(candidate)) continue;
      if (isInside(realpath(candidate), pkgDir, platform)) continue;
      if (!found.includes(candidate)) found.push(candidate);
    }
  }
  return found;
}

function which(name, opts) {
  return whichAll(name, opts)[0] || null;
}

function isWindowsAppsStub(file) {
  return /[\\/]WindowsApps[\\/]/i.test(file);
}

// --- uv -------------------------------------------------------------------

function findUv(opts = {}) {
  const platform = opts.platform || process.platform;
  const home = opts.home || os.homedir();
  const isExec = opts.isExecutable || ((f) => isExecutableFile(f, platform));
  const onPath = which('uv', opts);
  if (onPath) return onPath;
  // The uv installer drops it here but only edits shell profiles, so a
  // fresh install is often not on PATH in the shell npm was started from.
  const join = isWindows(platform) ? path.win32.join : path.posix.join;
  const exe = isWindows(platform) ? 'uv.exe' : 'uv';
  for (const dir of [join(home, '.local', 'bin'), join(home, '.cargo', 'bin')]) {
    const candidate = join(dir, exe);
    if (isExec(candidate)) return candidate;
  }
  return null;
}

// --- Python ---------------------------------------------------------------

function pythonCandidates(opts = {}) {
  const platform = opts.platform || process.platform;
  const candidates = [];
  if (isWindows(platform)) {
    // WindowsApps holds both the Store's "python" stub, which only prints
    // an install hint, and real Store-installed Pythons. Try it last and let
    // the version probe tell them apart.
    const pys = whichAll('py', opts);
    const pythons = whichAll('python', opts);
    const ordered = [
      ...pys.filter((f) => !isWindowsAppsStub(f)).map((f) => ({ f, py: true })),
      ...pythons.filter((f) => !isWindowsAppsStub(f)).map((f) => ({ f, py: false })),
      ...pys.filter(isWindowsAppsStub).map((f) => ({ f, py: true })),
      ...pythons.filter(isWindowsAppsStub).map((f) => ({ f, py: false })),
    ];
    for (const { f, py } of ordered) {
      if (!py) {
        candidates.push({ command: f, args: [] });
        continue;
      }
      for (const minor of [13, 12, 11, 10]) candidates.push({ command: f, args: [`-3.${minor}`] });
      // Newer than 3.13, or a launcher configured with only a default.
      candidates.push({ command: f, args: ['-3'] });
    }
    return candidates;
  }
  for (const name of ['python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3']) {
    for (const exe of whichAll(name, opts)) {
      // Running Apple's stub pops the "install command line developer
      // tools" dialog instead of failing quietly.
      if (platform === 'darwin' && exe === '/usr/bin/python3') continue;
      if (!candidates.some((c) => c.command === exe)) candidates.push({ command: exe, args: [] });
    }
  }
  return candidates;
}

function parseProbeOutput(stdout) {
  const match = /\((\d+),\s*(\d+)\)/.exec(String(stdout || ''));
  return match ? [Number(match[1]), Number(match[2])] : null;
}

function versionAtLeast(version, minimum) {
  return version[0] > minimum[0] || (version[0] === minimum[0] && version[1] >= minimum[1]);
}

// Runs the probe and collects its stdout: {status, stdout}.
async function execProbe(command, args, session) {
  let stdout = '';
  let closed = Promise.resolve();
  const spawn = (cmd, argv, options) => {
    const child = childProcess.spawn(cmd, argv, options);
    const timer = setTimeout(() => child.kill('SIGKILL'), PROBE_TIMEOUT_MS);
    // 'exit' can come before the last of stdout; 'close' comes after it.
    closed = new Promise((resolve) => {
      child.once('close', resolve);
      child.once('error', resolve);
    }).finally(() => clearTimeout(timer));
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (d) => (stdout += d));
    return child;
  };
  const result = await superviseChild(command, args, { stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true }, session, spawn);
  await closed;
  return { status: result.error ? null : result.code, stdout };
}

async function probePython(candidate, exec = execProbe) {
  const result = await exec(candidate.command, [...candidate.args, '-c', PROBE_CODE]);
  const version = parseProbeOutput(result.stdout);
  if (!version) return { version: null, ok: false };
  return { version, ok: result.status === 0 && versionAtLeast(version, MIN_PYTHON), hasVenv: result.status === 0 };
}

// Returns the first usable Python plus what was seen on the way, so the
// failure message can say "found 3.9" or "found 3.12 without venv".
async function findPython(opts = {}) {
  const session = opts.session;
  const probe = opts.probe || ((c) => probePython(c, (cmd, args) => execProbe(cmd, args, session)));
  const seen = [];
  for (const candidate of pythonCandidates(opts)) {
    throwIfInterrupted(session);
    const info = await probe(candidate);
    throwIfInterrupted(session);
    if (!info.version) continue;
    seen.push({ ...candidate, ...info });
    if (info.ok) return { python: candidate, seen };
  }
  return { python: null, seen };
}

// --- Cache directory ------------------------------------------------------

function cacheRootSpecs(opts = {}) {
  const env = opts.env || process.env;
  const platform = opts.platform || process.platform;
  const home = opts.home || os.homedir();
  const tmpdir = opts.tmpdir || os.tmpdir();
  const uid = opts.uid !== undefined ? opts.uid : process.getuid ? process.getuid() : 'user';
  const p = isWindows(platform) ? path.win32 : path.posix;
  let base;
  if (isWindows(platform)) {
    // Windows only ever builds one-run venvs (see cacheReuse), so there is
    // no shared %TEMP% fallback to trust: a predictable name in a directory
    // others may reach is exactly what cannot be validated there.
    base = env.LOCALAPPDATA && p.isAbsolute(env.LOCALAPPDATA) ? env.LOCALAPPDATA : p.join(home, 'AppData', 'Local');
    return [{ dir: p.join(base, 'deepgram-cli', 'venvs'), shared: false }];
  }
  if (platform === 'darwin') base = p.join(home, 'Library', 'Caches');
  else base = env.XDG_CACHE_HOME && p.isAbsolute(env.XDG_CACHE_HOME) ? env.XDG_CACHE_HOME : p.join(home, '.cache');
  // The fallback covers containers run as an arbitrary UID with a HOME that
  // does not exist or is read-only. It sits in a directory every user can
  // write to, so it is held to a stricter ownership check (see rootTrusted).
  return [
    { dir: p.join(base, 'deepgram-cli', 'venvs'), shared: false },
    { dir: p.join(tmpdir, `deepgram-cli-${uid}`, 'venvs'), shared: true },
  ];
}

function cacheRoots(opts = {}) {
  return cacheRootSpecs(opts).map((spec) => spec.dir);
}

// Whether a venv left by an earlier run may be executed again. Reuse is only
// safe when the cache root can be shown to be ours (rootTrusted). On Windows
// that would mean checking owners, ACLs and reparse points on every
// ancestor, which Node cannot do, so Windows never reuses a venv: it runs
// through uv, or builds a private venv for the one run and deletes it.
function cacheReuse(opts = {}) {
  if (opts.cacheReuse !== undefined) return opts.cacheReuse;
  return !isWindows(opts.platform || process.platform);
}

function currentUid(opts) {
  if (opts.uid !== undefined && typeof opts.uid === 'number') return opts.uid;
  return process.getuid ? process.getuid() : null;
}

const STICKY = 0o1000;

// Whatever is in a cache root gets executed, so the root and its parent
// must be ours: real directories (not symlinks) owned by this uid that no
// one else can write to. In a shared root such as /tmp, another user could
// otherwise pre-create the directory and plant a "cached" deepctl, so it
// must also be private (0700).
//
// Every ancestor above them matters too: whoever can rename entries in an
// ancestor can swap our cache for theirs between our check and our exec.
// So each ancestor must belong to root or to us, and if others can write to
// it, it must be sticky (like /tmp), which stops them renaming our entries.
// Windows has no uid, and its ACLs and reparse points are out of reach from
// Node, so no Windows root is trusted (see cacheReuse).
function rootTrusted(spec, opts = {}) {
  const platform = opts.platform || process.platform;
  if (isWindows(platform)) return false;
  const uid = currentUid(opts);
  const lstat = opts.lstat || fs.lstatSync;
  const forbidden = spec.shared ? 0o077 : 0o022;
  const statDir = (dir) => {
    try {
      const st = lstat(dir);
      return st.isDirectory() ? st : null;
    } catch {
      return null;
    }
  };
  const top = path.posix.dirname(spec.dir);
  for (const dir of [top, spec.dir]) {
    const st = statDir(dir);
    if (!st || st.uid !== uid || (st.mode & forbidden) !== 0) return false;
  }
  // Both the path as written (whose symlinks, like macOS's /var, must not
  // be replaceable) and the real path it resolves to.
  const realpath = opts.realpath || fs.realpathSync;
  const above = path.posix.dirname(top);
  let real;
  try {
    real = realpath(above);
  } catch {
    return false;
  }
  const gid = opts.gid !== undefined ? opts.gid : process.getgid ? process.getgid() : null;
  return ancestorsSafe(above, uid, gid, lstat, true) && ancestorsSafe(real, uid, gid, lstat, false);
}

// Group-writable is trusted only for a directory we own whose group id
// equals our uid: the user-private-group convention (Ubuntu, Fedora; umask
// 002), where ~/.cache is 0775 with a group named after us. A shared
// primary group (SUSE and Debian USERGROUPS=no use `users`, macOS uses
// `staff`) has other members who could swap the cache during the first
// build, so anything else group-writable fails closed.
function ancestorsSafe(start, uid, gid, lstat, allowLinks) {
  for (let dir = start; ; dir = path.posix.dirname(dir)) {
    let st;
    try {
      st = lstat(dir);
    } catch {
      return false;
    }
    const link = st.isSymbolicLink ? st.isSymbolicLink() : false;
    if (link ? !allowLinks : !st.isDirectory()) return false;
    if (st.uid !== 0 && st.uid !== uid) return false;
    const privateGroup = st.uid === uid && st.gid === gid && gid === uid;
    const writableByOthers = (st.mode & 0o002) !== 0 || ((st.mode & 0o020) !== 0 && !privateGroup);
    if (!link && writableByOthers && (st.mode & STICKY) === 0) return false;
    if (dir === '/' || dir === '.') return true;
  }
}

function mkdirPrivate(dir) {
  try {
    fs.mkdirSync(dir, { mode: 0o700 });
  } catch (err) {
    if (err.code !== 'EEXIST') throw err;
  }
}

// Creates the root if needed and returns whether it is safe and writable.
function prepareRoot(spec, opts = {}) {
  const platform = opts.platform || process.platform;
  const p = isWindows(platform) ? path.win32 : path.posix;
  const top = p.dirname(spec.dir);
  try {
    // 0700 so that, under umask 002, we do not create a group-writable
    // ~/.cache ourselves.
    fs.mkdirSync(p.dirname(top), { recursive: true, mode: 0o700 });
    mkdirPrivate(top);
    mkdirPrivate(spec.dir);
    if (!rootTrusted(spec, opts)) return false;
    fs.accessSync(spec.dir, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

function venvLayout(dir, platform = process.platform) {
  const p = isWindows(platform) ? path.win32 : path.posix;
  const bin = isWindows(platform) ? p.join(dir, 'Scripts') : p.join(dir, 'bin');
  const ext = isWindows(platform) ? '.exe' : '';
  return {
    dir,
    marker: p.join(dir, '.complete'),
    python: p.join(bin, `python${ext}`),
    deepctl: p.join(bin, `deepctl${ext}`),
  };
}

function findCachedVenv(key, opts = {}) {
  if (!cacheReuse(opts)) return null;
  const exists = opts.exists || fs.existsSync;
  const trusted = opts.trustRoot || ((spec) => rootTrusted(spec, opts));
  const p = isWindows(opts.platform || process.platform) ? path.win32 : path.posix;
  for (const spec of cacheRootSpecs(opts)) {
    if (!trusted(spec)) continue;
    const layout = venvLayout(p.join(spec.dir, key), opts.platform);
    if (venvUsable(layout, exists)) return layout;
  }
  return null;
}

// A venv's python is a link to the interpreter that built it. When that
// interpreter is uninstalled (brew, pyenv, a distro upgrade), every console
// script fails with a confusing ENOENT, so a venv without a working python
// is treated as missing and rebuilt. existsSync follows the link.
function venvUsable(layout, exists = fs.existsSync) {
  return exists(layout.marker) && exists(layout.deepctl) && exists(layout.python);
}

// Marks a venv as recently used, which keeps pruneSiblings away from it.
function touchMarker(marker) {
  try {
    const now = new Date();
    fs.utimesSync(marker, now, now);
  } catch {
    // Best effort: a read-only cache only loses the recency hint.
  }
}

// --- Lock -----------------------------------------------------------------

// Only for retrying a full stderr pipe, never for waiting on setup.
function sleepSync(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM';
  }
}

// owner.json is only ever replaced whole (write a temp file, rename it
// over), so a reader sees the old or the new owner, never a torn one.
// Every write goes through here.
function writeOwner(lockDir, owner, writeFile = fs.writeFileSync) {
  const file = path.join(lockDir, 'owner.json');
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    writeFile(tmp, JSON.stringify(owner));
    fs.renameSync(tmp, file);
  } catch (err) {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      // Removed with the lock directory anyway.
    }
    throw err;
  }
}

// {owner, raw} when owner.json holds an owner, else {owner: null}: missing
// (the owner is between mkdir and its first write) or unreadable (written
// non-atomically by an older launcher, or not JSON at all).
function readOwner(lockDir) {
  let raw;
  try {
    raw = fs.readFileSync(path.join(lockDir, 'owner.json'), 'utf8');
  } catch {
    return { owner: null, raw: null };
  }
  try {
    const owner = JSON.parse(raw);
    if (owner && typeof owner.pid === 'number') return { owner, raw };
  } catch {
    // Fall through.
  }
  return { owner: null, raw };
}

// Gives a missing or unreadable owner.json a moment to appear before the
// caller falls back to judging the lock by its directory's age.
async function readOwnerSettled(lockDir, tries = 5, gapMs = 20) {
  for (let i = 1; ; i++) {
    const read = readOwner(lockDir);
    if (read.owner || i >= tries) return read;
    await delay(gapMs);
  }
}

function lockIsStale(lockDir, now = Date.now(), alive = pidAlive, read = readOwner(lockDir)) {
  // No owner (see readOwner): fall back to the directory's age.
  const owner = read.owner;
  if (owner) {
    if (owner.host === os.hostname()) {
      // Our own pid in someone else's lock means the pid was reused: a
      // container that crashed mid-setup restarts as pid 1 with the same
      // hostname and cache volume. A live pid is otherwise trusted for a
      // slow install, up to an hour, after which it must be a reused pid.
      if (owner.pid === process.pid) return true;
      if (!alive(owner.pid)) return true;
      return now - owner.time > LOCK_MAX_MS;
    }
    // Across hosts (a shared home directory) the pid means nothing.
    return now - owner.time > LOCK_STALE_MS;
  }
  try {
    return now - fs.statSync(lockDir).mtimeMs > LOCK_STALE_MS;
  } catch {
    return false;
  }
}

// mkdir is atomic on every platform and filesystem npm runs on, unlike
// O_EXCL files on some network mounts. The wait between polls is a timer,
// not a blocking sleep, so a signal can end it (opts.session).
async function acquireLock(lockDir, opts = {}) {
  const session = opts.session;
  const waitMs = opts.waitMs !== undefined ? opts.waitMs : LOCK_WAIT_MS;
  const pollMs = opts.pollMs || LOCK_POLL_MS;
  const onWait = opts.onWait || (() => {});
  const deadline = Date.now() + waitMs;
  let announced = false;
  const writeFile = opts.writeFile || fs.writeFileSync;
  for (;;) {
    throwIfInterrupted(session);
    let created = false;
    try {
      fs.mkdirSync(lockDir);
      created = true;
      const owner = { pid: process.pid, host: os.hostname(), time: Date.now() };
      writeOwner(lockDir, owner, writeFile);
      const release = () => releaseLock(lockDir, owner);
      release.lock = { dir: lockDir, owner };
      return release;
    } catch (err) {
      if (created) fs.rmSync(lockDir, { recursive: true, force: true });
      if (err.code !== 'EEXIST' || created) {
        throw new LauncherError(`Could not create the setup lock ${lockDir}: ${err.message}`);
      }
    }
    if (lockIsStale(lockDir, Date.now(), pidAlive, await readOwnerSettled(lockDir))) {
      breakStaleLock(lockDir);
      continue;
    }
    if (Date.now() >= deadline) {
      throw new LauncherError(`Timed out waiting for another dg setup to finish (lock: ${lockDir}).`);
    }
    if (!announced) {
      onWait();
      announced = true;
    }
    try {
      await delay(pollMs, undefined, session ? { signal: session.abort.signal } : undefined);
    } catch (err) {
      if (err.name !== 'AbortError') throw err;
    }
  }
}

function readOwnerRaw(lockDir) {
  return readOwner(lockDir).raw;
}

// Two waiters can both judge the same lock stale. If each simply deleted
// it, the slower one could delete the lock the faster one has just taken.
// Instead, move the lock aside under a unique name (rename is atomic, so
// only one waiter gets the stale lock) and delete it only if what moved is
// still the owner that was judged stale. If a fresh lock was moved by
// mistake, try to put it back; the caller then simply retries.
//
// A residual interleaving remains with three or more waiters (one restores
// a lock another has already replaced). Directory locks cannot close it
// completely, and it needs a stale lock plus three concurrent first runs;
// the worst case is two builds of the same venv racing, which is accepted.
function breakStaleLock(lockDir) {
  const judged = readOwnerRaw(lockDir);
  const grave = `${lockDir}.stale.${process.pid}.${crypto.randomBytes(4).toString('hex')}`;
  try {
    fs.renameSync(lockDir, grave);
  } catch {
    return; // Someone else moved or removed it first.
  }
  if (readOwnerRaw(grave) === judged) {
    killOrphanedSetup(judged, lockDir.replace(/\.lock$/, ''));
    fs.rmSync(grave, { recursive: true, force: true });
    return;
  }
  try {
    fs.renameSync(grave, lockDir);
  } catch {
    // A new lock exists already; leave the moved one for pruning.
  }
}

function recordSetupGroup(lock, pgid) {
  try {
    lock.owner.pgid = pgid;
    writeOwner(lock.dir, lock.owner);
  } catch {
    // Best effort: without it a stale-lock breaker just cannot stop the group.
  }
}

// A launcher killed with SIGKILL cannot stop its setup step, whose pip keeps
// writing into the venv directory the next run is about to rebuild. The
// stale lock names that step's process group; kill it, but only on Linux
// (where /proc can show it) and only if some process in the group still
// mentions the venv directory on its command line, so a pgid reused by an
// unrelated process is left alone.
// The directory itself or a path inside it, never a sibling that merely
// shares a prefix (`venvs/0.4.1` must not match `venvs/0.4.10`).
function mentionsDir(arg, dir) {
  return arg === dir || arg.startsWith(dir + path.sep);
}

function killOrphanedSetup(ownerRaw, venvDir, procDir = '/proc') {
  let owner;
  try {
    owner = JSON.parse(ownerRaw);
  } catch {
    return false;
  }
  const pgid = owner && owner.pgid;
  if (!Number.isInteger(pgid) || pgid <= 1 || owner.host !== os.hostname()) return false;
  let pids;
  try {
    pids = fs.readdirSync(procDir).filter((e) => /^\d+$/.test(e));
  } catch {
    return false; // No /proc (macOS): nothing safe to check against.
  }
  const ours = pids.some((pid) => {
    try {
      const stat = fs.readFileSync(path.join(procDir, pid, 'stat'), 'utf8');
      const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
      if (Number(fields[2]) !== pgid) return false;
      return fs.readFileSync(path.join(procDir, pid, 'cmdline'), 'utf8').split('\0').some((arg) => mentionsDir(arg, venvDir));
    } catch {
      return false;
    }
  });
  if (!ours) return false;
  try {
    process.kill(-pgid, 'SIGKILL');
  } catch {
    // ESRCH: it exited on its own.
  }
  return true;
}

// Only removes the lock if it is still ours: if it was judged stale and
// taken over while we ran, the new owner's lock must survive.
function releaseLock(lockDir, owner) {
  // Gone already, or not readable: either way it is not ours to remove.
  const current = readOwner(lockDir).owner;
  if (current && current.pid === owner.pid && current.time === owner.time && current.host === owner.host) {
    try {
      fs.rmSync(lockDir, { recursive: true, force: true });
    } catch {
      // Best effort: a lock left behind is judged stale by the next run.
    }
  }
}

// --- Venv bootstrap -------------------------------------------------------

const CRASH_SIGNALS = new Set(['SIGSEGV', 'SIGBUS', 'SIGILL', 'SIGFPE', 'SIGABRT']);

async function runStep(command, args, what, hint, opts = {}) {
  const session = opts.session;
  throwIfInterrupted(session);
  // stdout goes to stderr: `dg -o json` and `dg mcp` own stdout. A process
  // group of its own (POSIX) lets an interrupt take down pip's children too.
  const options = { stdio: ['ignore', 2, 2], windowsHide: true, detached: !isWindows(process.platform) };
  const result = await superviseChild(command, args, options, session, opts.spawn);
  throwIfInterrupted(session);
  if (result.error) throw new LauncherError(`${what} failed: ${result.error.message}.`, hint);
  if (result.code !== 0) {
    const how = result.signal ? `killed by ${result.signal}` : `exit ${result.code}`;
    throw new LauncherError(`${what} failed (${how}).`, hint);
  }
}

const VENV_HINT = 'On Debian or Ubuntu, install python3-venv (sudo apt install python3-venv), or install uv instead.';
const NETWORK_HINT = 'Check your network connection and that PyPI is reachable, then run dg again.';

// Removes other versions' venvs, but only ones nobody has used for a long
// time: a dg pinned to an older npm version (npx @deepgram/cli@0.4.0, a
// project-local install) may be running from one right now. Every run from
// a cached venv refreshes its .complete mtime.
function pruneSiblings(root, keep, now = Date.now()) {
  let entries;
  try {
    entries = fs.readdirSync(root);
  } catch {
    return;
  }
  for (const entry of entries) {
    if (entry === keep || entry.endsWith('.lock')) continue;
    try {
      const lock = path.join(root, `${entry}.lock`);
      if (fs.existsSync(lock) && !lockIsStale(lock, now)) continue;
      const dir = path.join(root, entry);
      let idle;
      try {
        idle = now - fs.statSync(path.join(dir, '.complete')).mtimeMs > PRUNE_AFTER_MS;
      } catch {
        // No marker: a build that died. Leave it until no live build could own it.
        idle = now - fs.statSync(dir).mtimeMs > LOCK_STALE_MS;
      }
      if (idle) fs.rmSync(dir, { recursive: true, force: true });
    } catch {
      // Best effort: an old version left behind only costs disk space.
    }
  }
}

// Builds a venv for this run only, in a directory created fresh with an
// unpredictable name, so nothing another process prepared can be picked up.
// The caller deletes it once deepctl exits.
async function buildPrivateVenv({ python, key, spec, version, opts = {} }) {
  const stderr = opts.stderr || fdStderr;
  const p = isWindows(opts.platform || process.platform) ? path.win32 : path.posix;
  const base = cacheRootSpecs(opts)[0].dir;
  let dir;
  try {
    fs.mkdirSync(base, { recursive: true, mode: 0o700 });
    pruneOneRunVenvs(base, p);
    dir = fs.mkdtempSync(p.join(base, `${key}-`));
  } catch (err) {
    throw new LauncherError(`No writable directory for the Deepgram CLI: ${err.message}`, 'Install uv instead: it needs no setup on each run.');
  }
  const layout = venvLayout(dir, opts.platform);
  stderr.write(`Setting up Deepgram CLI ${version} for this run (~1 min each time; install uv to skip it: ${UV_INSTALL_WINDOWS})...\n`);
  try {
    await installInto(python, layout, spec, opts);
  } catch (err) {
    fs.rmSync(dir, { recursive: true, force: true });
    throw err;
  }
  return { ...layout, ephemeral: true };
}

// A one-run venv outlives its run when cleanup never happens: Node
// crashed, the process was killed (Task Manager, taskkill /F), or a closing
// console's few seconds of grace ran out before deepctl exited. Remove those, but only once they are old enough that no setup
// could still be building them, and only if they can be moved aside first:
// Windows refuses to rename a directory while a running deepctl has files
// open in it, so a long session's venv survives.
function pruneOneRunVenvs(base, p = path, now = Date.now()) {
  let entries;
  try {
    entries = fs.readdirSync(base);
  } catch {
    return;
  }
  for (const entry of entries) {
    // mkdtemp names (<key>-XXXXXX) and ones a previous prune moved aside.
    if (!/-[A-Za-z0-9]{6}(\.del)?$/.test(entry)) continue;
    const dir = p.join(base, entry);
    try {
      if (now - fs.statSync(dir).mtimeMs <= ONE_RUN_STALE_MS) continue;
      const grave = entry.endsWith('.del') ? dir : `${dir}.del`;
      if (grave !== dir) fs.renameSync(dir, grave);
      fs.rmSync(grave, { recursive: true, force: true });
    } catch {
      // In use, or already gone: leave it for a later run.
    }
  }
}

async function installInto(python, layout, spec, opts) {
  const pip = [layout.python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-q'];
  await runStep(python.command, [...python.args, '-m', 'venv', layout.dir], 'Creating the Python venv', VENV_HINT, opts);
  await runStep(pip[0], [...pip.slice(1), '-U', 'pip'], 'Upgrading pip', NETWORK_HINT, opts);
  await runStep(pip[0], [...pip.slice(1), spec], `Installing ${spec}`, NETWORK_HINT, opts);
}

// The lock is released in `finally`, not from process.on('exit'): that hook
// never runs when the process dies from a signal. run() turns signals into
// an Interrupted error instead, so this unwinds normally.
async function buildVenv({ python, key, spec, version, opts = {} }) {
  if (!cacheReuse(opts)) return buildPrivateVenv({ python, key, spec, version, opts });
  const stderr = opts.stderr || fdStderr;
  const prepare = opts.prepareRoot || ((spec) => prepareRoot(spec, opts));
  const rootSpec = cacheRootSpecs(opts).find(prepare);
  const root = rootSpec && rootSpec.dir;
  if (!root) throw new LauncherError('No writable cache directory for the Deepgram CLI.', 'Set XDG_CACHE_HOME to a writable directory, or install uv.');
  // Built in place: venv console scripts carry absolute shebangs, so a venv
  // built elsewhere and renamed into place would not run.
  const layout = venvLayout(path.join(root, key), opts.platform);
  const release = await acquireLock(`${layout.dir}.lock`, {
    ...opts.lock,
    session: opts.session,
    onWait: () => stderr.write('Waiting for another dg setup to finish...\n'),
  });
  if (opts.session) opts.session.lock = release.lock;
  try {
    if (venvUsable(layout)) return layout;
    // Half-built, or broken by a removed base interpreter: start over.
    fs.rmSync(layout.dir, { recursive: true, force: true });
    stderr.write(`Setting up Deepgram CLI ${version} (one-time, ~1 min)...\n`);
    try {
      await installInto(python, layout, spec, opts);
      fs.writeFileSync(layout.marker, `${spec}\n`);
    } catch (err) {
      fs.rmSync(layout.dir, { recursive: true, force: true });
      throw err;
    }
    pruneSiblings(root, key);
    return layout;
  } finally {
    if (opts.session) opts.session.lock = null;
    release();
  }
}

// --- Child environment and exit mapping ------------------------------------

// npx and `npm exec` unpack packages under npm's cache in a `_npx`
// directory. Where this package lives is what counts, not how the parent
// was started: a global dg run from inside an npx process is still global.
function launcherKind(pkgDir = PKG_DIR) {
  return pkgDir.split(/[\\/]/).includes('_npx') ? 'npx' : 'npm';
}

function childEnv(env, strategy, pkgDir = PKG_DIR) {
  const out = { ...env };
  // A deepctl the user installed themselves knows its own install method;
  // telling it "npm" would make `dg update` give the wrong advice.
  if (strategy === 'path') delete out.DEEPCTL_LAUNCHER;
  else out.DEEPCTL_LAUNCHER = launcherKind(pkgDir);
  return out;
}

function exitAction(code, signal, platform = process.platform) {
  if (code !== null && code !== undefined) return { exit: code };
  if (signal && !isWindows(platform)) {
    const num = os.constants.signals[signal];
    return { signal, exit: num ? 128 + num : 1 };
  }
  return { exit: 1 };
}

// --- Plan -----------------------------------------------------------------

function installHelp(seen) {
  const lines = ['', 'The Deepgram CLI needs uv or Python 3.10+ (with venv) to run. Fix one of:', ''];
  lines.push('  1. Install uv (recommended), then run dg again:');
  lines.push('       curl -LsSf https://astral.sh/uv/install.sh | sh');
  lines.push(`       ${UV_INSTALL_WINDOWS}   (Windows)`);
  lines.push('     On Windows, the launcher only runs real uv.exe / python.exe files: pyenv-win\'s');
  lines.push('     python.bat shims and uv.cmd are skipped, since a .bat or .cmd needs a shell');
  lines.push('     that would re-parse your arguments.');
  const noVenv = seen.find((s) => s.version && versionAtLeast(s.version, MIN_PYTHON) && !s.hasVenv);
  if (noVenv) {
    lines.push(`  2. Python ${noVenv.version.join('.')} was found without venv/ensurepip. Install it:`);
    lines.push('       sudo apt install python3-venv   (Debian/Ubuntu)');
  } else {
    const old = seen.find((s) => s.version && !versionAtLeast(s.version, MIN_PYTHON));
    const found = old ? ` (found ${old.version.join('.')})` : '';
    lines.push(`  2. Install Python 3.10 or newer${found}.`);
  }
  lines.push('  3. Or install the CLI directly:');
  lines.push('       curl -fsSL https://deepgram.com/install.sh | sh');
  lines.push('       brew install deepgram/tap/deepgram');
  return `${lines.join('\n')}\n`;
}

// Decides how to run deepctl. Resolves to {command, args, strategy}, plus
// {cleanup} for a one-run venv; may build a venv as a side effect.
async function resolveRunner(argv, opts = {}) {
  const env = opts.env || process.env;
  const version = opts.version || packageVersion();
  const spec = pypiSpec(env, version);
  const key = venvKey(env, version);
  const forced = env.DEEPCTL_NPM_RUNNER || '';
  if (forced && !RUNNERS.includes(forced)) {
    throw new LauncherError(`DEEPCTL_NPM_RUNNER must be one of ${RUNNERS.join(', ')} (got "${forced}").`);
  }

  if (forced === 'path') {
    const deepctl = which('deepctl', opts);
    if (!deepctl) throw new LauncherError('DEEPCTL_NPM_RUNNER=path, but no deepctl was found on PATH.');
    return { command: deepctl, args: argv, strategy: 'path' };
  }

  if (!forced || forced === 'venv') {
    const cached = findCachedVenv(key, opts);
    if (cached) {
      touchMarker(cached.marker);
      return { command: cached.deepctl, args: argv, strategy: 'venv' };
    }
  }

  if (!forced || forced === 'uvx') {
    const uv = findUv(opts);
    if (uv) return { command: uv, args: ['tool', 'run', '--from', spec, 'deepctl', ...argv], strategy: 'uvx' };
    if (forced) throw new LauncherError('DEEPCTL_NPM_RUNNER=uvx, but uv was not found.');
  }

  const { python, seen } = await findPython(opts);
  if (!python) throw new LauncherError(installHelp(seen));
  const layout = await buildVenv({ python, key, spec, version, opts });
  const runner = { command: layout.deepctl, args: argv, strategy: 'venv' };
  if (layout.ephemeral) runner.cleanup = layout.dir;
  return runner;
}

async function run(argv = process.argv.slice(2), opts = {}) {
  const env = opts.env || process.env;
  const stderr = opts.stderr || fdStderr;
  const session = createSession();

  // Handlers go in before any setup starts. During setup, any of these
  // signals interrupts it (setup children have their own process group, so
  // only we can tell them). Once deepctl runs:
  //
  // TERM and HUP come from whoever manages *this* process: pass them on.
  // Ctrl-C and Ctrl-\ from a terminal reach the child directly (same
  // process group), so forwarding them at once would deliver them twice.
  // But an IDE, supervisor or `timeout -s INT` signals only our pid; if the
  // child is still running after a grace period, it never got the signal.
  //
  // With a terminal attached, assume the signal came from it and reached the
  // child too: a second, forwarded interrupt would cut short a child that
  // is still cleaning up after the first. On Windows a console Ctrl-C
  // reaches every attached process, and child.kill() would terminate the
  // child outright rather than interrupt it.
  // isatty(), not process.stdin.isTTY: see fdStderr for why.
  const terminal = [0, 1, 2].some((fd) => tty.isatty(fd));
  const timers = new Set();
  const childRunning = (child) => child.exitCode === null && child.signalCode === null;
  const handle = (sig, forwardNow) => () => {
    if (session.phase === 'setup') {
      interruptSetup(session, sig);
      return;
    }
    const child = session.child;
    if (!child) return;
    if (forwardNow) {
      child.kill(sig);
      return;
    }
    if (isWindows(process.platform) || terminal) return;
    const timer = setTimeout(() => {
      timers.delete(timer);
      if (childRunning(child)) child.kill(sig);
    }, SIGNAL_GRACE_MS);
    timers.add(timer);
  };
  // Known limitation: a signal the parent set to "ignore" (nohup, or
  // `trap '' INT` before a background job) cannot be passed on. Node resets
  // inherited SIG_IGN dispositions to the default before any JS runs, so
  // neither /proc/self/status nor ps can see them here, and deepctl starts
  // with the defaults. See "Known limitation" in README.md.
  //
  // On Windows, Node emits SIGHUP when the console window closes (Windows
  // ends the process a few seconds later) and SIGBREAK on Ctrl-Break.
  // Handling both is what lets a one-run venv be removed on the way out.
  const handlers = { SIGINT: handle('SIGINT', false), SIGTERM: handle('SIGTERM', true), SIGHUP: handle('SIGHUP', true) };
  if (isWindows(process.platform)) handlers.SIGBREAK = handle('SIGBREAK', false);
  else handlers.SIGQUIT = handle('SIGQUIT', false);
  for (const [sig, fn] of Object.entries(handlers)) process.on(sig, fn);
  const removeHandlers = () => {
    for (const [sig, fn] of Object.entries(handlers)) process.removeListener(sig, fn);
  };

  // A one-run venv (Windows without uv) goes once deepctl is done with it.
  let runner;
  const cleanup = () => {
    if (!runner || !runner.cleanup) return;
    try {
      fs.rmSync(runner.cleanup, { recursive: true, force: true, maxRetries: 3 });
    } catch {
      // Best effort: a leftover one-run venv only costs disk space.
    }
  };

  try {
    runner = await resolveRunner(argv, { ...opts, session });
    throwIfInterrupted(session);
  } catch (err) {
    removeHandlers();
    cleanup();
    if (session.interrupted) {
      // The setup child is gone, its partial venv removed and the lock
      // released (buildVenv's finally); now die from the same signal.
      const sig = session.interrupted;
      stderr.write(`dg: setup interrupted (${sig}).\n`);
      if (isWindows(process.platform)) process.exit(signalExitCode(sig));
      dieBySignal(sig, signalExitCode(sig));
      return;
    }
    // Filesystem surprises (a cache directory changing underneath us) get
    // the same one-line treatment as expected failures, not a stack trace.
    const hint = err instanceof LauncherError && err.hint ? `\n${err.hint}` : '';
    stderr.write(`dg: ${String(err.message || err).trimStart()}${hint}\n`);
    process.exit(1);
  }

  // No await between here and spawn: no signal can slip in unhandled.
  session.phase = 'run';
  const child = childProcess.spawn(runner.command, runner.args, {
    stdio: 'inherit',
    env: childEnv(env, runner.strategy),
  });
  session.child = child;

  child.on('error', (err) => {
    stderr.write(`dg: could not start ${runner.command}: ${err.message}\n`);
    cleanup();
    process.exit(1);
  });

  child.on('exit', (code, signal) => {
    cleanup();
    for (const timer of timers) clearTimeout(timer);
    removeHandlers();
    const action = exitAction(code, signal);
    // A crash otherwise surfaces only as a bare exit status (139 and so on).
    if (CRASH_SIGNALS.has(signal)) stderr.write(`dg: ${runner.command} crashed (${signal}).\n`);
    if (action.signal) {
      // Die the same way the child did.
      dieBySignal(action.signal, action.exit);
      return;
    }
    process.exit(action.exit);
  });
}

module.exports = {
  Interrupted,
  LauncherError,
  acquireLock,
  cacheReuse,
  cacheRootSpecs,
  pruneOneRunVenvs,
  cacheRoots,
  breakStaleLock,
  childEnv,
  launcherKind,
  exitAction,
  exeNames,
  findCachedVenv,
  killOrphanedSetup,
  readOwner,
  writeOwner,
  mentionsDir,
  findPython,
  findUv,
  installHelp,
  lockIsStale,
  packageVersion,
  parseProbeOutput,
  probePython,
  pathDirs,
  writeStderr,
  prepareRoot,
  pypiSpec,
  pruneSiblings,
  pythonCandidates,
  releaseLock,
  resolveRunner,
  rootTrusted,
  run,
  venvKey,
  venvLayout,
  venvUsable,
  versionAtLeast,
  which,
  whichAll,
};
