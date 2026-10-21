'use strict';

// Runs the Python Deepgram CLI (deepctl, from PyPI) at the same version as
// this npm package. Nothing here is downloaded at install time: the first
// `dg` either hands off to uv, reuses a cached venv, or builds one.

const childProcess = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const PKG_DIR = path.resolve(__dirname, '..');
const MIN_PYTHON = [3, 10];
const LOCK_WAIT_MS = 5 * 60 * 1000;
const LOCK_STALE_MS = 10 * 60 * 1000;
// No first-run install takes this long; past it a same-host lock is
// abandoned even if its pid has been reused by an unrelated process.
const LOCK_MAX_MS = 60 * 60 * 1000;
const LOCK_POLL_MS = 250;
const PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000;
const SIGNAL_GRACE_MS = 1000;
const PROBE_TIMEOUT_MS = 10 * 1000;
const RUNNERS = ['uvx', 'venv', 'path'];

// Prints the version first so a Python without venv/ensurepip (Debian's
// python3 without python3-venv) still reports what it is.
const PROBE_CODE = 'import sys;print(tuple(sys.version_info[:2]));import venv,ensurepip';

class LauncherError extends Error {
  constructor(message, hint = '') {
    super(message);
    this.hint = hint;
  }
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

function probePython(candidate, spawnSync = childProcess.spawnSync) {
  const result = spawnSync(candidate.command, [...candidate.args, '-c', PROBE_CODE], {
    encoding: 'utf8',
    timeout: PROBE_TIMEOUT_MS,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const version = parseProbeOutput(result.stdout);
  if (!version) return { version: null, ok: false };
  return { version, ok: result.status === 0 && versionAtLeast(version, MIN_PYTHON), hasVenv: result.status === 0 };
}

// Returns the first usable Python plus what was seen on the way, so the
// failure message can say "found 3.9" or "found 3.12 without venv".
function findPython(opts = {}) {
  const probe = opts.probe || probePython;
  const seen = [];
  for (const candidate of pythonCandidates(opts)) {
    const info = probe(candidate);
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
  if (isWindows(platform)) base = env.LOCALAPPDATA || p.join(home, 'AppData', 'Local');
  else if (platform === 'darwin') base = p.join(home, 'Library', 'Caches');
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
// Windows has no uid and a per-user LOCALAPPDATA.
function rootTrusted(spec, opts = {}) {
  const platform = opts.platform || process.platform;
  if (isWindows(platform)) return true;
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
  return ancestorsSafe(above, uid, lstat, true) && ancestorsSafe(real, uid, lstat, false);
}

function ancestorsSafe(start, uid, lstat, allowLinks) {
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
    if (!link && (st.mode & 0o022) !== 0 && (st.mode & STICKY) === 0) return false;
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
    fs.mkdirSync(p.dirname(top), { recursive: true });
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

function lockIsStale(lockDir, now = Date.now(), alive = pidAlive) {
  let owner = null;
  try {
    owner = JSON.parse(fs.readFileSync(path.join(lockDir, 'owner.json'), 'utf8'));
  } catch {
    // The owner may be between mkdir and writing owner.json; fall back to
    // the directory's age.
  }
  if (owner && typeof owner.pid === 'number') {
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
// O_EXCL files on some network mounts.
function acquireLock(lockDir, opts = {}) {
  const waitMs = opts.waitMs !== undefined ? opts.waitMs : LOCK_WAIT_MS;
  const pollMs = opts.pollMs || LOCK_POLL_MS;
  const onWait = opts.onWait || (() => {});
  const deadline = Date.now() + waitMs;
  let announced = false;
  const writeFile = opts.writeFile || fs.writeFileSync;
  for (;;) {
    let created = false;
    try {
      fs.mkdirSync(lockDir);
      created = true;
      const owner = { pid: process.pid, host: os.hostname(), time: Date.now() };
      writeFile(path.join(lockDir, 'owner.json'), JSON.stringify(owner));
      return () => releaseLock(lockDir, owner);
    } catch (err) {
      if (created) fs.rmSync(lockDir, { recursive: true, force: true });
      if (err.code !== 'EEXIST' || created) {
        throw new LauncherError(`Could not create the setup lock ${lockDir}: ${err.message}`);
      }
    }
    if (lockIsStale(lockDir)) {
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
    sleepSync(pollMs);
  }
}

function readOwnerRaw(lockDir) {
  try {
    return fs.readFileSync(path.join(lockDir, 'owner.json'), 'utf8');
  } catch {
    return null;
  }
}

// Two waiters can both judge the same lock stale. If each simply deleted
// it, the slower one could delete the lock the faster one has just taken.
// Instead, move the lock aside under a unique name (rename is atomic, so
// only one waiter gets the stale lock) and delete it only if what moved is
// still the owner that was judged stale. If a fresh lock was moved by
// mistake, try to put it back; the caller then simply retries.
function breakStaleLock(lockDir) {
  const judged = readOwnerRaw(lockDir);
  const grave = `${lockDir}.stale.${process.pid}.${crypto.randomBytes(4).toString('hex')}`;
  try {
    fs.renameSync(lockDir, grave);
  } catch {
    return; // Someone else moved or removed it first.
  }
  if (readOwnerRaw(grave) === judged) {
    fs.rmSync(grave, { recursive: true, force: true });
    return;
  }
  try {
    fs.renameSync(grave, lockDir);
  } catch {
    // A new lock exists already; leave the moved one for pruning.
  }
}

// Only removes the lock if it is still ours: if it was judged stale and
// taken over while we ran, the new owner's lock must survive.
function releaseLock(lockDir, owner) {
  try {
    const current = JSON.parse(fs.readFileSync(path.join(lockDir, 'owner.json'), 'utf8'));
    if (current.pid === owner.pid && current.time === owner.time && current.host === owner.host) {
      fs.rmSync(lockDir, { recursive: true, force: true });
    }
  } catch {
    // Gone already, or not readable: either way it is not ours to remove.
  }
}

// --- Venv bootstrap -------------------------------------------------------

function runStep(command, args, what, hint, spawnSync) {
  // stdout goes to stderr: `dg -o json` and `dg mcp` own stdout.
  const result = spawnSync(command, args, { stdio: ['ignore', 2, 2], windowsHide: true });
  if (result.error) throw new LauncherError(`${what} failed: ${result.error.message}.`, hint);
  if (result.status !== 0) throw new LauncherError(`${what} failed (exit ${result.status}).`, hint);
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

function buildVenv({ python, key, spec, version, opts = {} }) {
  const stderr = opts.stderr || process.stderr;
  const spawnSync = opts.spawnSync || childProcess.spawnSync;
  const prepare = opts.prepareRoot || ((spec) => prepareRoot(spec, opts));
  const rootSpec = cacheRootSpecs(opts).find(prepare);
  const root = rootSpec && rootSpec.dir;
  if (!root) throw new LauncherError('No writable cache directory for the Deepgram CLI.', 'Set XDG_CACHE_HOME to a writable directory, or install uv.');
  // Built in place: venv console scripts carry absolute shebangs, so a venv
  // built elsewhere and renamed into place would not run.
  const layout = venvLayout(path.join(root, key), opts.platform);
  const release = acquireLock(`${layout.dir}.lock`, {
    ...opts.lock,
    onWait: () => stderr.write('Waiting for another dg setup to finish...\n'),
  });
  const onExit = () => release();
  process.once('exit', onExit);
  try {
    if (venvUsable(layout)) return layout;
    // Half-built, or broken by a removed base interpreter: start over.
    fs.rmSync(layout.dir, { recursive: true, force: true });
    stderr.write(`Setting up Deepgram CLI ${version} (one-time, ~1 min)...\n`);
    const pip = [layout.python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-q'];
    try {
      runStep(python.command, [...python.args, '-m', 'venv', layout.dir], 'Creating the Python venv', VENV_HINT, spawnSync);
      runStep(pip[0], [...pip.slice(1), '-U', 'pip'], 'Upgrading pip', NETWORK_HINT, spawnSync);
      runStep(pip[0], [...pip.slice(1), spec], `Installing ${spec}`, NETWORK_HINT, spawnSync);
      fs.writeFileSync(layout.marker, `${spec}\n`);
    } catch (err) {
      fs.rmSync(layout.dir, { recursive: true, force: true });
      throw err;
    }
    pruneSiblings(root, key);
    return layout;
  } finally {
    process.removeListener('exit', onExit);
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
  lines.push('       powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   (Windows)');
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

// Decides how to run deepctl. Returns {command, args, strategy}; may build a
// venv as a side effect.
function resolveRunner(argv, opts = {}) {
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

  const { python, seen } = findPython(opts);
  if (!python) throw new LauncherError(installHelp(seen));
  const layout = buildVenv({ python, key, spec, version, opts });
  return { command: layout.deepctl, args: argv, strategy: 'venv' };
}

function run(argv = process.argv.slice(2), opts = {}) {
  const env = opts.env || process.env;
  const stderr = opts.stderr || process.stderr;

  let runner;
  try {
    runner = resolveRunner(argv, opts);
  } catch (err) {
    // Filesystem surprises (a cache directory changing underneath us) get
    // the same one-line treatment as expected failures, not a stack trace.
    const hint = err instanceof LauncherError && err.hint ? `\n${err.hint}` : '';
    stderr.write(`dg: ${String(err.message || err).trimStart()}${hint}\n`);
    process.exit(1);
  }

  const child = childProcess.spawn(runner.command, runner.args, {
    stdio: 'inherit',
    env: childEnv(env, runner.strategy),
  });

  // TERM and HUP come from whoever manages *this* process: pass them on.
  // Ctrl-C and Ctrl-\ from a terminal reach the child directly (same
  // process group), so forwarding them at once would deliver them twice.
  // But an IDE, supervisor or `timeout -s INT` signals only our pid; if the
  // child is still running after a grace period, it never got the signal.
  const timers = new Set();
  const childRunning = () => child.exitCode === null && child.signalCode === null;
  const forward = (sig) => () => child.kill(sig);
  const forwardIfMissed = (sig) => () => {
    const timer = setTimeout(() => {
      timers.delete(timer);
      if (childRunning()) child.kill(sig);
    }, SIGNAL_GRACE_MS);
    timers.add(timer);
  };
  //
  // With a terminal attached, assume the signal came from it and reached the
  // child too: a second, forwarded interrupt would cut short a child that
  // is still cleaning up after the first. On Windows a console Ctrl-C
  // reaches every attached process, and child.kill() would terminate the
  // child outright rather than interrupt it.
  const terminal = Boolean(process.stdin.isTTY || process.stdout.isTTY || process.stderr.isTTY);
  const interrupt = (sig) => (isWindows(process.platform) || terminal ? () => {} : forwardIfMissed(sig));
  const handlers = { SIGINT: interrupt('SIGINT'), SIGTERM: forward('SIGTERM') };
  if (!isWindows(process.platform)) {
    handlers.SIGQUIT = interrupt('SIGQUIT');
    handlers.SIGHUP = forward('SIGHUP');
  }
  for (const [sig, fn] of Object.entries(handlers)) process.on(sig, fn);

  child.on('error', (err) => {
    stderr.write(`dg: could not start ${runner.command}: ${err.message}\n`);
    process.exit(1);
  });

  child.on('exit', (code, signal) => {
    for (const timer of timers) clearTimeout(timer);
    for (const [sig, fn] of Object.entries(handlers)) process.removeListener(sig, fn);
    const action = exitAction(code, signal);
    if (action.signal) {
      // Die the same way the child did, so shells and supervisors see the
      // signal rather than a plain exit code.
      process.removeAllListeners(action.signal);
      try {
        process.kill(process.pid, action.signal);
      } catch {
        // Fall through to the numeric exit below.
      }
      setTimeout(() => process.exit(action.exit), 100);
      return;
    }
    process.exit(action.exit);
  });
}

module.exports = {
  LauncherError,
  acquireLock,
  cacheRootSpecs,
  cacheRoots,
  breakStaleLock,
  childEnv,
  launcherKind,
  exitAction,
  exeNames,
  findCachedVenv,
  findPython,
  findUv,
  installHelp,
  lockIsStale,
  packageVersion,
  parseProbeOutput,
  probePython,
  pathDirs,
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
