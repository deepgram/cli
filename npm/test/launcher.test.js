'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test, describe } = require('node:test');

const launcher = require('../lib/launcher.js');

const PKG_DIR = path.resolve(__dirname, '..');
const BIN = path.join(PKG_DIR, 'bin', 'dg.js');

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'dg-launcher-test-'));
}

function fakeExe(dir, name, body = '#!/bin/sh\nexit 0\n') {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, body);
  fs.chmodSync(file, 0o755);
  return file;
}

const posixOnly = { skip: process.platform === 'win32' && 'POSIX shell scripts' };

describe('package.json', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(PKG_DIR, 'package.json'), 'utf8'));

  test('every bin points at one target, so `npx @deepgram/cli` resolves', () => {
    assert.deepEqual(new Set(Object.values(pkg.bin)), new Set(['bin/dg.js']));
    assert.deepEqual(Object.keys(pkg.bin).sort(), ['deepgram', 'dg']);
  });

  test('ships no deepctl bin, dependencies or install scripts', () => {
    assert.equal(pkg.bin.deepctl, undefined);
    assert.equal(pkg.dependencies, undefined);
    for (const hook of ['preinstall', 'install', 'postinstall', 'prepare']) {
      assert.equal(pkg.scripts && pkg.scripts[hook], undefined);
    }
  });

  test('version is a plain release version', () => {
    assert.match(launcher.packageVersion(PKG_DIR), /^\d+\.\d+\.\d+$/);
  });
});

describe('PyPI spec', () => {
  test('defaults to the package version', () => {
    assert.equal(launcher.pypiSpec({}, '1.2.3'), 'deepctl==1.2.3');
    assert.equal(launcher.venvKey({}, '1.2.3'), '1.2.3');
  });

  test('DEEPCTL_NPM_PYPI_SPEC overrides it and gets its own venv key', () => {
    const env = { DEEPCTL_NPM_PYPI_SPEC: 'deepctl==1.0.0' };
    assert.equal(launcher.pypiSpec(env, '1.2.3'), 'deepctl==1.0.0');
    assert.match(launcher.venvKey(env, '1.2.3'), /^1\.2\.3\+[0-9a-f]{12}$/);
  });
});

describe('PATH resolution', () => {
  test('skips empty and relative entries', () => {
    assert.deepEqual(launcher.pathDirs({ PATH: ':/usr/bin::./bin:bin:/opt/x' }, 'linux'), ['/usr/bin', '/opt/x']);
  });

  test('Windows reads Path, splits on ; and only accepts .exe/.com', () => {
    const env = { Path: 'C:\\Tools;;.\\local;D:\\bin' };
    assert.deepEqual(launcher.pathDirs(env, 'win32'), ['C:\\Tools', 'D:\\bin']);
    assert.deepEqual(launcher.exeNames('uv', 'win32'), ['uv.exe', 'uv.com']);
    const seen = [];
    const found = launcher.whichAll('uv', {
      env,
      platform: 'win32',
      pkgDir: 'C:\\npm\\node_modules\\@deepgram\\cli',
      realpath: (f) => f,
      isExecutable: (f) => {
        seen.push(f);
        return f === 'D:\\bin\\uv.exe';
      },
    });
    assert.deepEqual(found, ['D:\\bin\\uv.exe']);
    assert.ok(seen.every((f) => /\.(exe|com)$/.test(f)));
  });

  test('finds executables in PATH order', posixOnly, () => {
    const dir = tmpdir();
    const a = fakeExe(path.join(dir, 'a'), 'tool');
    const b = fakeExe(path.join(dir, 'b'), 'tool');
    fs.writeFileSync(path.join(dir, 'c-tool'), 'not executable');
    const env = { PATH: [path.join(dir, 'a'), path.join(dir, 'b')].join(':') };
    assert.deepEqual(launcher.whichAll('tool', { env, platform: 'linux' }), [a, b]);
  });

  test('never resolves into the launcher package (recursion guard)', posixOnly, () => {
    const dir = tmpdir();
    const pkgDir = path.join(dir, 'node_modules', '@deepgram', 'cli');
    const inside = fakeExe(path.join(pkgDir, 'bin'), 'deepctl');
    const shims = path.join(dir, 'shims');
    fs.mkdirSync(shims);
    fs.symlinkSync(inside, path.join(shims, 'deepctl'));
    const real = fakeExe(path.join(dir, 'real'), 'deepctl');
    const env = { PATH: [shims, path.join(pkgDir, 'bin'), path.join(dir, 'real')].join(':') };
    assert.deepEqual(launcher.whichAll('deepctl', { env, pkgDir, platform: 'linux' }), [real]);
  });
});

describe('uv discovery', () => {
  test('prefers PATH, then ~/.local/bin, then ~/.cargo/bin', () => {
    const present = new Set(['/home/u/.local/bin/uv', '/home/u/.cargo/bin/uv']);
    const opts = { env: { PATH: '/usr/bin' }, platform: 'linux', home: '/home/u', realpath: (f) => f, isExecutable: (f) => present.has(f) };
    assert.equal(launcher.findUv(opts), '/home/u/.local/bin/uv');
    present.add('/usr/bin/uv');
    assert.equal(launcher.findUv(opts), '/usr/bin/uv');
    present.clear();
    assert.equal(launcher.findUv(opts), null);
  });

  test('Windows fallbacks use uv.exe', () => {
    const opts = {
      env: { Path: 'C:\\Windows' },
      platform: 'win32',
      pkgDir: 'C:\\npm\\node_modules\\@deepgram\\cli',
      home: 'C:\\Users\\u',
      realpath: (f) => f,
      isExecutable: (f) => f === 'C:\\Users\\u\\.cargo\\bin\\uv.exe',
    };
    assert.equal(launcher.findUv(opts), 'C:\\Users\\u\\.cargo\\bin\\uv.exe');
  });
});

describe('Python discovery', () => {
  test('parses the probe output', () => {
    assert.deepEqual(launcher.parseProbeOutput('(3, 12)\n'), [3, 12]);
    assert.deepEqual(launcher.parseProbeOutput('(3, 9)\nTraceback...'), [3, 9]);
    assert.equal(launcher.parseProbeOutput(''), null);
    assert.equal(launcher.parseProbeOutput(undefined), null);
  });

  test('compares versions', () => {
    assert.ok(launcher.versionAtLeast([3, 10], [3, 10]));
    assert.ok(launcher.versionAtLeast([4, 0], [3, 10]));
    assert.ok(!launcher.versionAtLeast([3, 9], [3, 10]));
  });

  test('POSIX probe order, skipping the macOS /usr/bin/python3 stub', () => {
    const present = new Set(['/usr/bin/python3', '/usr/local/bin/python3.11', '/usr/local/bin/python3']);
    const opts = { env: { PATH: '/usr/local/bin:/usr/bin' }, realpath: (f) => f, isExecutable: (f) => present.has(f) };
    const darwin = launcher.pythonCandidates({ ...opts, platform: 'darwin' }).map((c) => c.command);
    assert.deepEqual(darwin, ['/usr/local/bin/python3.11', '/usr/local/bin/python3']);
    const linux = launcher.pythonCandidates({ ...opts, platform: 'linux' }).map((c) => c.command);
    assert.deepEqual(linux, ['/usr/local/bin/python3.11', '/usr/local/bin/python3', '/usr/bin/python3']);
  });

  test('Windows tries py -3.13..-3.10, then python, skipping the Store stub', () => {
    const present = new Set([
      'C:\\Windows\\py.exe',
      'C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe',
      'C:\\Python312\\python.exe',
    ]);
    const opts = {
      env: { Path: 'C:\\Windows;C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps;C:\\Python312' },
      platform: 'win32',
      pkgDir: 'C:\\npm\\node_modules\\@deepgram\\cli',
      realpath: (f) => f,
      isExecutable: (f) => present.has(f),
    };
    const got = launcher.pythonCandidates(opts).map((c) => [c.command, ...c.args].join(' '));
    assert.deepEqual(got, [
      'C:\\Windows\\py.exe -3.13',
      'C:\\Windows\\py.exe -3.12',
      'C:\\Windows\\py.exe -3.11',
      'C:\\Windows\\py.exe -3.10',
      'C:\\Python312\\python.exe',
    ]);
  });

  test('findPython returns the first usable one and records the rest', () => {
    const present = new Set(['/usr/bin/python3.12', '/usr/bin/python3']);
    const probes = {
      '/usr/bin/python3.12': { version: [3, 12], ok: false, hasVenv: false },
      '/usr/bin/python3': { version: [3, 11], ok: true, hasVenv: true },
    };
    const opts = {
      env: { PATH: '/usr/bin' },
      platform: 'linux',
      realpath: (f) => f,
      isExecutable: (f) => present.has(f),
      probe: (c) => probes[c.command],
    };
    const { python, seen } = launcher.findPython(opts);
    assert.equal(python.command, '/usr/bin/python3');
    assert.equal(seen.length, 2);
  });

  test('the help text names the fix for what was found', () => {
    const noVenv = launcher.installHelp([{ version: [3, 12], ok: false, hasVenv: false }]);
    assert.match(noVenv, /python3-venv/);
    assert.match(noVenv, /astral\.sh\/uv\/install\.sh/);
    assert.match(noVenv, /brew install deepgram\/tap\/deepgram/);
    const old = launcher.installHelp([{ version: [3, 9], ok: false, hasVenv: true }]);
    assert.match(old, /found 3\.9/);
    assert.doesNotMatch(old, /python3-venv/);
  });
});

describe('cache directory', () => {
  test('Linux honours an absolute XDG_CACHE_HOME', () => {
    const roots = launcher.cacheRoots({ env: { XDG_CACHE_HOME: '/xdg' }, platform: 'linux', home: '/home/u', tmpdir: '/tmp', uid: 7 });
    assert.deepEqual(roots, ['/xdg/deepgram-cli/venvs', '/tmp/deepgram-cli-7/venvs']);
    const relative = launcher.cacheRoots({ env: { XDG_CACHE_HOME: 'rel' }, platform: 'linux', home: '/home/u', tmpdir: '/tmp', uid: 7 });
    assert.equal(relative[0], '/home/u/.cache/deepgram-cli/venvs');
  });

  test('macOS uses ~/Library/Caches', () => {
    const roots = launcher.cacheRoots({ env: {}, platform: 'darwin', home: '/Users/u', tmpdir: '/tmp', uid: 501 });
    assert.equal(roots[0], '/Users/u/Library/Caches/deepgram-cli/venvs');
  });

  test('Windows uses LOCALAPPDATA', () => {
    const roots = launcher.cacheRoots({ env: { LOCALAPPDATA: 'C:\\Users\\u\\AppData\\Local' }, platform: 'win32', home: 'C:\\Users\\u', tmpdir: 'C:\\Temp', uid: 'user' });
    assert.deepEqual(roots, ['C:\\Users\\u\\AppData\\Local\\deepgram-cli\\venvs', 'C:\\Temp\\deepgram-cli-user\\venvs']);
    const layout = launcher.venvLayout('C:\\v', 'win32');
    assert.equal(layout.deepctl, 'C:\\v\\Scripts\\deepctl.exe');
  });

  test('a cached venv is used only when complete, from either root', () => {
    const opts = { env: {}, platform: 'linux', home: '/home/u', tmpdir: '/tmp', uid: 1 };
    const files = new Set(['/tmp/deepgram-cli-1/venvs/1.0.0/.complete', '/tmp/deepgram-cli-1/venvs/1.0.0/bin/deepctl']);
    const hit = launcher.findCachedVenv('1.0.0', { ...opts, exists: (f) => files.has(f) });
    assert.equal(hit.deepctl, '/tmp/deepgram-cli-1/venvs/1.0.0/bin/deepctl');
    files.delete('/tmp/deepgram-cli-1/venvs/1.0.0/.complete');
    assert.equal(launcher.findCachedVenv('1.0.0', { ...opts, exists: (f) => files.has(f) }), null);
  });
});

describe('lock', () => {
  test('acquire, then release', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    const release = launcher.acquireLock(lock);
    const owner = JSON.parse(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8'));
    assert.equal(owner.pid, process.pid);
    release();
    assert.equal(fs.existsSync(lock), false);
  });

  test('a lock held by a live process times out and announces the wait once', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    const release = launcher.acquireLock(lock);
    let waits = 0;
    assert.throws(
      () => launcher.acquireLock(lock, { waitMs: 300, pollMs: 50, onWait: () => waits++ }),
      /Timed out waiting/,
    );
    assert.equal(waits, 1);
    release();
  });

  test('a lock whose pid is dead is stale and is taken over', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 999999, host: os.hostname(), time: Date.now() }));
    assert.equal(launcher.lockIsStale(lock, Date.now(), () => false), true);
    assert.equal(launcher.lockIsStale(lock, Date.now(), () => true), false);
    const release = launcher.acquireLock(lock, { waitMs: 0 });
    release();
  });

  test('an old lock is stale even if its pid looks alive', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: process.pid, host: os.hostname(), time: 0 }));
    assert.equal(launcher.lockIsStale(lock), true);
  });

  test('a lock from another host is not judged by local pids', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 999999, host: 'elsewhere', time: Date.now() }));
    assert.equal(launcher.lockIsStale(lock, Date.now(), () => false), false);
  });
});

describe('child environment', () => {
  test('marks the launcher and increments the depth', () => {
    const env = launcher.childEnv({ HOME: '/h' }, 'uvx');
    assert.equal(env.DEEPCTL_LAUNCHER, 'npm');
    assert.equal(env.DEEPCTL_NPM_LAUNCHER_DEPTH, '1');
    assert.equal(env.HOME, '/h');
    assert.equal(launcher.childEnv({}, 'venv').DEEPCTL_LAUNCHER, 'npm');
  });

  test('the path strategy removes DEEPCTL_LAUNCHER', () => {
    const env = launcher.childEnv({ DEEPCTL_LAUNCHER: 'npm' }, 'path');
    assert.equal('DEEPCTL_LAUNCHER' in env, false);
  });
});

describe('exit mapping', () => {
  test('passes exit codes through', () => {
    assert.deepEqual(launcher.exitAction(0, null, 'linux'), { exit: 0 });
    assert.deepEqual(launcher.exitAction(2, null, 'linux'), { exit: 2 });
  });

  test('re-raises signals on POSIX with a 128+n fallback', () => {
    assert.deepEqual(launcher.exitAction(null, 'SIGTERM', 'linux'), { signal: 'SIGTERM', exit: 128 + os.constants.signals.SIGTERM });
    assert.deepEqual(launcher.exitAction(null, 'SIGINT', 'darwin'), { signal: 'SIGINT', exit: 128 + os.constants.signals.SIGINT });
  });

  test('Windows has no signal to re-raise', () => {
    assert.deepEqual(launcher.exitAction(null, 'SIGTERM', 'win32'), { exit: 1 });
  });
});

describe('resolveRunner', () => {
  test('rejects an unknown DEEPCTL_NPM_RUNNER', () => {
    assert.throws(() => launcher.resolveRunner([], { env: { DEEPCTL_NPM_RUNNER: 'pipx' }, version: '1.0.0' }), /must be one of/);
  });

  test('uses uv with the pinned spec when no venv is cached', () => {
    const r = launcher.resolveRunner(['--version'], {
      env: { PATH: '/bin' },
      version: '1.2.3',
      platform: 'linux',
      home: '/nonexistent',
      tmpdir: '/nonexistent',
      realpath: (f) => f,
      isExecutable: (f) => f === '/bin/uv',
      exists: () => false,
    });
    assert.deepEqual(r, { command: '/bin/uv', args: ['tool', 'run', '--from', 'deepctl==1.2.3', 'deepctl', '--version'], strategy: 'uvx' });
  });

  test('a cached venv wins over uv', () => {
    const r = launcher.resolveRunner(['x'], {
      env: { PATH: '/bin' },
      version: '1.2.3',
      platform: 'linux',
      home: '/h',
      tmpdir: '/t',
      uid: 0,
      realpath: (f) => f,
      isExecutable: (f) => f === '/bin/uv',
      exists: (f) => f.startsWith('/h/.cache/deepgram-cli/venvs/1.2.3/'),
    });
    assert.equal(r.strategy, 'venv');
    assert.equal(r.command, '/h/.cache/deepgram-cli/venvs/1.2.3/bin/deepctl');
  });

  test('DEEPCTL_NPM_RUNNER=path requires a real deepctl', () => {
    const base = { version: '1.0.0', platform: 'linux', realpath: (f) => f };
    assert.throws(
      () => launcher.resolveRunner([], { ...base, env: { PATH: '/bin', DEEPCTL_NPM_RUNNER: 'path' }, isExecutable: () => false }),
      /no deepctl was found/,
    );
    const r = launcher.resolveRunner(['a'], {
      ...base,
      env: { PATH: '/bin', DEEPCTL_NPM_RUNNER: 'path' },
      isExecutable: (f) => f === '/bin/deepctl',
    });
    assert.deepEqual(r, { command: '/bin/deepctl', args: ['a'], strategy: 'path' });
  });

  test('DEEPCTL_NPM_RUNNER=uvx without uv is an error', () => {
    assert.throws(
      () => launcher.resolveRunner([], {
        env: { PATH: '/bin', DEEPCTL_NPM_RUNNER: 'uvx' },
        version: '1.0.0',
        platform: 'linux',
        home: '/nonexistent',
        realpath: (f) => f,
        isExecutable: () => false,
      }),
      /uv was not found/,
    );
  });
});

describe('bin/dg.js end to end', posixOnly, () => {
  function runBin(env, args = []) {
    return spawnSync(process.execPath, [BIN, ...args], { env, encoding: 'utf8' });
  }

  test('aborts when started recursively', () => {
    const r = runBin({ PATH: '/nonexistent', DEEPCTL_NPM_LAUNCHER_DEPTH: '1' });
    assert.equal(r.status, 1);
    assert.match(r.stderr, /recursively/);
  });

  test('no uv and no Python: clear error on stderr, nothing on stdout, exit 1', () => {
    const home = tmpdir();
    const r = runBin({ PATH: '/nonexistent', HOME: home, XDG_CACHE_HOME: path.join(home, 'c') });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.match(r.stderr, /needs uv or Python 3\.10\+/);
  });

  test('passes arguments, env and exit code through a fake uv', () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'uv', '#!/bin/sh\necho "$DEEPCTL_LAUNCHER $DEEPCTL_NPM_LAUNCHER_DEPTH $*"\nexit 7\n');
    const r = runBin({ PATH: bin, HOME: home, XDG_CACHE_HOME: path.join(home, 'c') }, ['listen', 'a b.wav']);
    assert.equal(r.status, 7);
    const version = launcher.packageVersion(PKG_DIR);
    assert.equal(r.stdout, `npm 1 tool run --from deepctl==${version} deepctl listen a b.wav\n`);
  });

  test('re-raises the signal the child died from', () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'uv', '#!/bin/sh\nkill -TERM $$\n');
    const r = runBin({ PATH: bin, HOME: home, XDG_CACHE_HOME: path.join(home, 'c') });
    assert.equal(r.status, null);
    assert.equal(r.signal, 'SIGTERM');
  });
});
