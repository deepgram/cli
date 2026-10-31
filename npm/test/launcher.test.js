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

// A lock owned by another live process on this host (the test runner).
function liveForeignLock(lock, time = Date.now()) {
  fs.mkdirSync(lock);
  fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: process.ppid, host: os.hostname(), time }));
}

// A stand-in for child_process.spawn: runs `body(command, args)` and exits
// with the code it returns, asynchronously, like a real child.
function fakeSpawn(body) {
  const { EventEmitter } = require('node:events');
  return (command, args) => {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.pid = 0;
    setImmediate(() => {
      child.exitCode = body(command, args);
      child.emit('exit', child.exitCode, null);
    });
    return child;
  };
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

  test('ships the same LICENSE as the repository', () => {
    const repoLicense = path.join(PKG_DIR, '..', 'LICENSE');
    const own = fs.readFileSync(path.join(PKG_DIR, 'LICENSE'), 'utf8');
    assert.match(own, /MIT License/);
    if (fs.existsSync(repoLicense)) assert.equal(own, fs.readFileSync(repoLicense, 'utf8'));
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

  test('Windows tries py -3.13..-3.10 and -3, then python, with WindowsApps last', () => {
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
      'C:\\Windows\\py.exe -3',
      'C:\\Python312\\python.exe',
      // A real Store Python lives here too; the probe rejects the stub.
      'C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe',
    ]);
  });

  test('Windows tries a py found only in WindowsApps, after everything else', () => {
    const apps = 'C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps';
    const present = new Set([`${apps}\\py.exe`, `${apps}\\python.exe`]);
    const opts = {
      env: { Path: apps },
      platform: 'win32',
      pkgDir: 'C:\\npm\\node_modules\\@deepgram\\cli',
      realpath: (f) => f,
      isExecutable: (f) => present.has(f),
    };
    const got = launcher.pythonCandidates(opts).map((c) => [c.command, ...c.args].join(' '));
    assert.equal(got.length, 6);
    assert.equal(got[0], `${apps}\\py.exe -3.13`);
    assert.equal(got[4], `${apps}\\py.exe -3`);
    assert.equal(got[5], `${apps}\\python.exe`);
  });

  test('the version probe rejects a Store stub that prints no version', async () => {
    const stub = async () => ({ status: 9009, stdout: '' });
    const info = await launcher.probePython({ command: 'python.exe', args: [] }, stub);
    assert.equal(info.ok, false);
    assert.equal(info.version, null);
  });

  test('findPython returns the first usable one and records the rest', async () => {
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
    const { python, seen } = await launcher.findPython(opts);
    assert.equal(python.command, '/usr/bin/python3');
    assert.equal(seen.length, 2);
  });

  test('the help text names the fix for what was found', () => {
    const noVenv = launcher.installHelp([{ version: [3, 12], ok: false, hasVenv: false }]);
    assert.match(noVenv, /python3-venv/);
    assert.match(noVenv, /astral\.sh\/uv\/install\.sh/);
    assert.match(noVenv, /brew install deepgram\/tap\/deepgram/);
    assert.match(noVenv, /python\.bat shims and uv\.cmd are skipped/);
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

  test('Windows uses LOCALAPPDATA only, with no %TEMP% fallback', () => {
    const roots = launcher.cacheRoots({ env: { LOCALAPPDATA: 'C:\\Users\\u\\AppData\\Local' }, platform: 'win32', home: 'C:\\Users\\u', tmpdir: 'C:\\Temp', uid: 'user' });
    assert.deepEqual(roots, ['C:\\Users\\u\\AppData\\Local\\deepgram-cli\\venvs']);
    // A relative LOCALAPPDATA is ignored, like a relative XDG_CACHE_HOME.
    const relative = launcher.cacheRoots({ env: { LOCALAPPDATA: 'AppData' }, platform: 'win32', home: 'C:\\Users\\u' });
    assert.deepEqual(relative, ['C:\\Users\\u\\AppData\\Local\\deepgram-cli\\venvs']);
    const layout = launcher.venvLayout('C:\\v', 'win32');
    assert.equal(layout.deepctl, 'C:\\v\\Scripts\\deepctl.exe');
  });

  test('a cached venv is used only when complete, from either root', () => {
    const opts = { env: {}, platform: 'linux', home: '/home/u', tmpdir: '/tmp', uid: 1, trustRoot: () => true };
    const files = new Set(['/tmp/deepgram-cli-1/venvs/1.0.0/.complete', '/tmp/deepgram-cli-1/venvs/1.0.0/bin/deepctl', '/tmp/deepgram-cli-1/venvs/1.0.0/bin/python']);
    const hit = launcher.findCachedVenv('1.0.0', { ...opts, exists: (f) => files.has(f) });
    assert.equal(hit.deepctl, '/tmp/deepgram-cli-1/venvs/1.0.0/bin/deepctl');
    files.delete('/tmp/deepgram-cli-1/venvs/1.0.0/.complete');
    assert.equal(launcher.findCachedVenv('1.0.0', { ...opts, exists: (f) => files.has(f) }), null);
  });

  test('a venv whose python no longer resolves is not used', () => {
    const opts = { env: {}, platform: 'linux', home: '/home/u', tmpdir: '/tmp', uid: 1, trustRoot: () => true };
    const files = new Set(['/home/u/.cache/deepgram-cli/venvs/1.0.0/.complete', '/home/u/.cache/deepgram-cli/venvs/1.0.0/bin/deepctl']);
    assert.equal(launcher.findCachedVenv('1.0.0', { ...opts, exists: (f) => files.has(f) }), null);
  });

  test('a dangling bin/python symlink makes a venv unusable (real filesystem)', posixOnly, () => {
    const dir = tmpdir();
    const layout = launcher.venvLayout(dir, 'linux');
    fs.mkdirSync(path.dirname(layout.python), { recursive: true });
    fs.writeFileSync(layout.marker, '');
    fakeExe(path.dirname(layout.deepctl), 'deepctl');
    fs.symlinkSync('/nonexistent/python3', layout.python);
    assert.equal(launcher.venvUsable(layout), false);
    fs.unlinkSync(layout.python);
    fs.symlinkSync(process.execPath, layout.python);
    assert.equal(launcher.venvUsable(layout), true);
  });

  test('an untrusted root is never used, however complete its venv looks', () => {
    const opts = { env: {}, platform: 'linux', home: '/home/u', tmpdir: '/tmp', uid: 1, trustRoot: (spec) => !spec.shared };
    const files = new Set(['/tmp/deepgram-cli-1/venvs/1.0.0/.complete', '/tmp/deepgram-cli-1/venvs/1.0.0/bin/deepctl']);
    assert.equal(launcher.findCachedVenv('1.0.0', { ...opts, exists: (f) => files.has(f) }), null);
  });
});

describe('Windows never reuses a cached venv', () => {
  const win = {
    env: { Path: 'C:\\bin', LOCALAPPDATA: 'C:\\Users\\u\\AppData\\Local' },
    platform: 'win32',
    home: 'C:\\Users\\u',
    pkgDir: 'C:\\npm\\node_modules\\@deepgram\\cli',
    realpath: (f) => f,
    // Every file "exists": a complete-looking venv sits in the cache.
    exists: () => true,
  };

  test('cache reuse is off on win32 and on elsewhere', () => {
    assert.equal(launcher.cacheReuse({ platform: 'win32' }), false);
    assert.equal(launcher.cacheReuse({ platform: 'linux' }), true);
    assert.equal(launcher.cacheReuse({ platform: 'darwin' }), true);
  });

  test('a complete-looking venv is ignored, even with a permissive trust check', () => {
    assert.equal(launcher.findCachedVenv('1.0.0', win), null);
    assert.equal(launcher.findCachedVenv('1.0.0', { ...win, trustRoot: () => true }), null);
  });

  test('resolveRunner goes to uv instead of the cached venv', async () => {
    const r = await launcher.resolveRunner(['--version'], { ...win, version: '1.0.0', isExecutable: (f) => f === 'C:\\bin\\uv.exe' });
    assert.equal(r.strategy, 'uvx');
    assert.equal(r.command, 'C:\\bin\\uv.exe');
  });

  test('without uv, the venv is built fresh for this run and marked for cleanup', posixOnly, async () => {
    // The win32 path logic is exercised on a POSIX filesystem by turning
    // reuse off directly; Windows CI runs the real thing (venv smoke test).
    const home = tmpdir();
    const root = path.join(home, 'c', 'deepgram-cli', 'venvs');
    fs.mkdirSync(path.join(root, '1.0.0', 'bin'), { recursive: true });
    const leaked = path.join(root, '1.0.0-Old123');
    fs.mkdirSync(leaked);
    const old = new Date(Date.now() - 2 * 60 * 60 * 1000);
    fs.utimesSync(leaked, old, old);
    for (const f of ['.complete', 'bin/deepctl', 'bin/python']) fs.writeFileSync(path.join(root, '1.0.0', f), '');
    const calls = [];
    const spawn = fakeSpawn((command, args) => {
      calls.push(args.slice(-2).join(' '));
      if (args.includes('venv')) fs.mkdirSync(path.join(args[args.length - 1], 'bin'), { recursive: true });
      return 0;
    });
    const lines = [];
    const r = await launcher.resolveRunner(['x'], {
      env: { PATH: '/usr/bin', XDG_CACHE_HOME: path.join(home, 'c') },
      version: '1.0.0',
      platform: 'linux',
      cacheReuse: false,
      probe: () => ({ version: [3, 12], ok: true, hasVenv: true }),
      isExecutable: (f) => f === '/usr/bin/python3',
      realpath: (f) => f,
      spawn,
      stderr: { write: (t) => lines.push(t) },
    });
    // Not the planted 1.0.0 venv: a new, unpredictably named sibling.
    assert.equal(path.dirname(r.cleanup), root);
    assert.match(path.basename(r.cleanup), /^1\.0\.0-.{6}$/);
    assert.equal(r.command, path.join(r.cleanup, 'bin', 'deepctl'));
    assert.equal(calls.length, 3);
    assert.match(lines.join(''), /for this run.*install uv.*install\.ps1/);
    assert.equal(lines.join('').trimEnd().split('\n').length, 1, 'the hint is one line');
    assert.equal(fs.existsSync(path.join(r.cleanup, '.complete')), false);
    assert.equal(fs.existsSync(leaked), false, 'a leaked one-run venv was pruned');
  });
});

describe('pruning one-run venvs', () => {
  const HOUR = 60 * 60 * 1000;
  function aged(root, name, ageMs) {
    const dir = path.join(root, name);
    fs.mkdirSync(path.join(dir, 'Scripts'), { recursive: true });
    const t = new Date(Date.now() - ageMs);
    fs.utimesSync(dir, t, t);
    return dir;
  }

  test('removes leaked one-run venvs over an hour old, of any version, and keeps the rest', () => {
    const root = tmpdir();
    const leaked = aged(root, '1.0.0-AbC123', 2 * HOUR);
    const olderVersion = aged(root, '0.9.0-xyz789', 3 * 24 * HOUR);
    const halfDeleted = aged(root, '1.0.0-Qq11Ww.del', 2 * HOUR);
    const concurrent = aged(root, '1.0.0-Zz99Yy', 5 * 60 * 1000);
    const unrelated = aged(root, 'notes', 2 * HOUR);
    launcher.pruneOneRunVenvs(root);
    assert.equal(fs.existsSync(leaked), false);
    assert.equal(fs.existsSync(`${leaked}.del`), false);
    assert.equal(fs.existsSync(olderVersion), false);
    assert.equal(fs.existsSync(halfDeleted), false);
    assert.equal(fs.existsSync(concurrent), true, 'a run still setting up keeps its venv');
    assert.equal(fs.existsSync(unrelated), true);
  });

  test('a venv that cannot be moved aside (in use on Windows) is left alone', () => {
    const root = tmpdir();
    const busy = aged(root, '1.0.0-AbC123', 2 * HOUR);
    const realRename = fs.renameSync;
    fs.renameSync = () => {
      throw Object.assign(new Error('EBUSY'), { code: 'EBUSY' });
    };
    try {
      launcher.pruneOneRunVenvs(root);
    } finally {
      fs.renameSync = realRename;
    }
    assert.equal(fs.existsSync(busy), true);
  });

  test('a missing directory is not an error', () => {
    assert.doesNotThrow(() => launcher.pruneOneRunVenvs(path.join(tmpdir(), 'nope')));
  });
});

describe('cache root ownership (POSIX)', () => {
  const dirStat = (uid, mode, isDir = true, gid = uid) => ({ uid, gid, mode: (isDir ? 0o040000 : 0o120000) | mode, isDirectory: () => isDir, isSymbolicLink: () => !isDir });
  const shared = { dir: '/tmp/deepgram-cli-1000/venvs', shared: true };
  const home = { dir: '/home/u/.cache/deepgram-cli/venvs', shared: false };
  // Sane ancestors; individual tests override entries.
  const ancestors = {
    '/': dirStat(0, 0o755),
    '/tmp': dirStat(0, 0o1777),
    '/home': dirStat(0, 0o755),
    '/home/u': dirStat(1000, 0o755),
    '/home/u/.cache': dirStat(1000, 0o700),
  };
  const check = (spec, stats, realpath = (d) => d, gid = 1000) => {
    const all = { ...ancestors, ...stats };
    return launcher.rootTrusted(spec, { platform: 'linux', uid: 1000, gid, realpath, lstat: (d) => {
      if (!(d in all)) throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
      return all[d];
    } });
  };

  test('a private directory we own under sticky /tmp is trusted', () => {
    assert.equal(check(shared, { '/tmp/deepgram-cli-1000': dirStat(1000, 0o700), '/tmp/deepgram-cli-1000/venvs': dirStat(1000, 0o700) }), true);
  });

  test('a shared root owned by another user is rejected', () => {
    assert.equal(check(shared, { '/tmp/deepgram-cli-1000': dirStat(2000, 0o700), '/tmp/deepgram-cli-1000/venvs': dirStat(1000, 0o700) }), false);
    assert.equal(check(shared, { '/tmp/deepgram-cli-1000': dirStat(1000, 0o700), '/tmp/deepgram-cli-1000/venvs': dirStat(2000, 0o700) }), false);
  });

  test('a shared root others can read or write is rejected, even if ours', () => {
    assert.equal(check(shared, { '/tmp/deepgram-cli-1000': dirStat(1000, 0o777), '/tmp/deepgram-cli-1000/venvs': dirStat(1000, 0o700) }), false);
    assert.equal(check(shared, { '/tmp/deepgram-cli-1000': dirStat(1000, 0o750), '/tmp/deepgram-cli-1000/venvs': dirStat(1000, 0o700) }), false);
  });

  test('a symlinked root is rejected', () => {
    assert.equal(check(shared, { '/tmp/deepgram-cli-1000': dirStat(1000, 0o777, false), '/tmp/deepgram-cli-1000/venvs': dirStat(1000, 0o700) }), false);
  });

  test('the home root may be readable by others but not writable', () => {
    const base = '/home/u/.cache/deepgram-cli';
    assert.equal(check(home, { [base]: dirStat(1000, 0o755), [`${base}/venvs`]: dirStat(1000, 0o755) }), true);
    assert.equal(check(home, { [base]: dirStat(1000, 0o775), [`${base}/venvs`]: dirStat(1000, 0o755) }), false);
    assert.equal(check(home, { [base]: dirStat(2000, 0o755), [`${base}/venvs`]: dirStat(1000, 0o755) }), false);
  });

  test('an ancestor another user can write to is rejected', () => {
    const base = '/home/u/.cache/deepgram-cli';
    const ok = { [base]: dirStat(1000, 0o700), [`${base}/venvs`]: dirStat(1000, 0o700) };
    assert.equal(check(home, ok), true);
    // HOME=/tmp style: ~/.cache made world-writable by someone else.
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(2000, 0o777) }), false);
    // Ours but group-writable by a shared group, not sticky: swappable by the group.
    assert.equal(check(home, { ...ok, '/home/u': dirStat(1000, 0o775, true, 50) }), false);
    // World-writable but sticky and root-owned, like /tmp: fine.
    assert.equal(check(home, { ...ok, '/home': dirStat(0, 0o1777) }), true);
    // Owned by a third user, even read-only: rejected.
    assert.equal(check(home, { ...ok, '/home': dirStat(2000, 0o755) }), false);
  });

  test('a root-owned symlink ancestor is allowed, an attacker-owned one is not', () => {
    const base = '/home/u/.cache/deepgram-cli';
    const ok = { [base]: dirStat(1000, 0o700), [`${base}/venvs`]: dirStat(1000, 0o700) };
    // /home -> /data/home, as /var -> /private/var on macOS.
    const real = (d) => d.replace(/^\/home/, '/data/home');
    const target = { '/data': dirStat(0, 0o755), '/data/home': dirStat(0, 0o755), '/data/home/u': dirStat(1000, 0o755), '/data/home/u/.cache': dirStat(1000, 0o700) };
    assert.equal(check(home, { ...ok, ...target, '/home': dirStat(0, 0o777, false) }, real), true);
    assert.equal(check(home, { ...ok, ...target, '/home': dirStat(2000, 0o777, false) }, real), false);
    // The resolved path is checked too.
    assert.equal(check(home, { ...ok, ...target, '/data/home': dirStat(2000, 0o777), '/home': dirStat(0, 0o777, false) }, real), false);
  });

  test('a missing root is not trusted', () => {
    assert.equal(check(shared, {}), false);
  });

  test('on a real filesystem, a 0777 directory we own is rejected and 0700 accepted', posixOnly, () => {
    const base = tmpdir();
    const spec = { dir: path.join(base, 'deepgram-cli-x', 'venvs'), shared: true };
    fs.mkdirSync(spec.dir, { recursive: true });
    fs.chmodSync(path.dirname(spec.dir), 0o777);
    fs.chmodSync(spec.dir, 0o700);
    assert.equal(launcher.rootTrusted(spec), false);
    fs.chmodSync(path.dirname(spec.dir), 0o700);
    assert.equal(launcher.rootTrusted(spec), true);
    const link = { dir: path.join(base, 'linked', 'venvs'), shared: true };
    fs.symlinkSync(path.dirname(spec.dir), path.join(base, 'linked'));
    assert.equal(launcher.rootTrusted(link), false);
  });

  test('our own group-writable ~/.cache in our private group is accepted (umask 002)', () => {
    const base = '/home/u/.cache/deepgram-cli';
    const ok = { [base]: dirStat(1000, 0o700), [`${base}/venvs`]: dirStat(1000, 0o700) };
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(1000, 0o775) }), true);
    assert.equal(check(home, { ...ok, '/home/u': dirStat(1000, 0o775) }), true);
    // Group-writable with a shared group (staff, users): rejected.
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(1000, 0o775, true, 50) }), false);
    // Writable by everyone: rejected even when ours.
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(1000, 0o777) }), false);
    // Group-writable, our gid, but owned by someone else: rejected.
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(2000, 0o775, true, 1000) }), false);
  });

  test('a group-writable ~/.cache in a shared primary group is rejected even though it is ours', () => {
    // SUSE / Debian USERGROUPS=no: primary group `users` (100), shared with
    // every other user, so gid matches ours but is not a private group.
    const base = '/home/u/.cache/deepgram-cli';
    const ok = { [base]: dirStat(1000, 0o700, true, 100), [`${base}/venvs`]: dirStat(1000, 0o700, true, 100) };
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(1000, 0o775, true, 100) }, (d) => d, 100), false);
    // The same directory without group write is fine.
    assert.equal(check(home, { ...ok, '/home/u/.cache': dirStat(1000, 0o755, true, 100) }, (d) => d, 100), true);
  });

  test('prepareRoot creates missing parents 0700 even under umask 002', posixOnly, () => {
    const home = tmpdir();
    const spec = { dir: path.join(home, '.cache', 'deepgram-cli', 'venvs'), shared: false };
    const old = process.umask(0o002);
    try {
      assert.equal(launcher.prepareRoot(spec), true);
    } finally {
      process.umask(old);
    }
    assert.equal(fs.statSync(path.join(home, '.cache')).mode & 0o777, 0o700);
    assert.equal(fs.statSync(spec.dir).mode & 0o777, 0o700);
  });

  test('no Windows root is trusted: ACLs and reparse points cannot be checked', () => {
    assert.equal(launcher.rootTrusted({ dir: 'C:\\x\\venvs', shared: false }, { platform: 'win32' }), false);
    assert.equal(launcher.rootTrusted({ dir: 'C:\\x\\venvs', shared: true }, { platform: 'win32' }), false);
  });
});

describe('pruning other versions', () => {
  const DAY = 24 * 60 * 60 * 1000;
  function venv(root, name, { markerAgeDays, dirAgeMs } = {}) {
    const dir = path.join(root, name);
    fs.mkdirSync(path.join(dir, 'bin'), { recursive: true });
    if (markerAgeDays !== undefined) {
      const marker = path.join(dir, '.complete');
      fs.writeFileSync(marker, '');
      const t = new Date(Date.now() - markerAgeDays * DAY);
      fs.utimesSync(marker, t, t);
    }
    if (dirAgeMs !== undefined) {
      const t = new Date(Date.now() - dirAgeMs);
      fs.utimesSync(dir, t, t);
    }
    return dir;
  }

  test('keeps recently used versions and removes long-idle ones', () => {
    const root = tmpdir();
    venv(root, '1.0.0', { markerAgeDays: 0 });
    const recent = venv(root, '0.9.0', { markerAgeDays: 2 });
    const idle = venv(root, '0.8.0', { markerAgeDays: 45 });
    launcher.pruneSiblings(root, '1.0.0');
    assert.equal(fs.existsSync(recent), true);
    assert.equal(fs.existsSync(idle), false);
  });

  test('a half-built venv is removed only once no build could still own it', () => {
    const root = tmpdir();
    const fresh = venv(root, '0.9.0', { dirAgeMs: 60 * 1000 });
    const dead = venv(root, '0.8.0', { dirAgeMs: 60 * 60 * 1000 });
    launcher.pruneSiblings(root, '1.0.0');
    assert.equal(fs.existsSync(fresh), true);
    assert.equal(fs.existsSync(dead), false);
  });

  test('never removes a version with a live lock', () => {
    const root = tmpdir();
    const locked = venv(root, '0.8.0', { markerAgeDays: 90 });
    liveForeignLock(`${locked}.lock`);
    launcher.pruneSiblings(root, '1.0.0');
    assert.equal(fs.existsSync(locked), true);
  });
});

describe('lock', () => {
  test('acquire, then release', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    const release = await launcher.acquireLock(lock);
    const owner = JSON.parse(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8'));
    assert.equal(owner.pid, process.pid);
    release();
    assert.equal(fs.existsSync(lock), false);
  });

  test('a lock held by a live process times out and announces the wait once', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    liveForeignLock(lock);
    let waits = 0;
    await assert.rejects(
      launcher.acquireLock(lock, { waitMs: 300, pollMs: 50, onWait: () => waits++ }),
      /Timed out waiting/,
    );
    assert.equal(waits, 1);
  });

  test('a lock whose pid is dead is stale and is taken over', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 999999, host: os.hostname(), time: Date.now() }));
    assert.equal(launcher.lockIsStale(lock, Date.now(), () => false), true);
    assert.equal(launcher.lockIsStale(lock, Date.now(), () => true), false);
    const release = await launcher.acquireLock(lock, { waitMs: 0 });
    release();
  });

  test('a same-host lock with a live pid is kept for up to an hour', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    liveForeignLock(lock, Date.now() - 30 * 60 * 1000);
    assert.equal(launcher.lockIsStale(lock), false);
    assert.equal(launcher.lockIsStale(lock, Date.now() + 31 * 60 * 1000), true);
  });

  test('a lock naming our own pid is a reused pid and stale (crashed container restart)', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: process.pid, host: os.hostname(), time: Date.now() }));
    assert.equal(launcher.lockIsStale(lock), true);
    const release = await launcher.acquireLock(lock, { waitMs: 0 });
    release();
    assert.equal(fs.existsSync(lock), false);
  });

  test('breaking a stale lock removes it only if it is still the judged owner', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 999999, host: os.hostname(), time: 1 }));
    launcher.breakStaleLock(lock);
    assert.equal(fs.existsSync(lock), false);
    assert.deepEqual(fs.readdirSync(path.dirname(lock)), []);
  });

  test('a fresh lock moved aside by a racing waiter is put back', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 1, host: 'h', time: 1 }));
    // Simulate the race: the owner changes between judging and renaming.
    const realRename = fs.renameSync;
    let swapped = false;
    fs.renameSync = (from, to) => {
      if (!swapped) {
        swapped = true;
        fs.writeFileSync(path.join(from, 'owner.json'), JSON.stringify({ pid: 2, host: 'h', time: 2 }));
      }
      return realRename(from, to);
    };
    try {
      launcher.breakStaleLock(lock);
    } finally {
      fs.renameSync = realRename;
    }
    assert.equal(JSON.parse(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8')).pid, 2);
  });

  test('a lock from another host is judged by age, not local pids', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 999999, host: 'elsewhere', time: Date.now() }));
    assert.equal(launcher.lockIsStale(lock, Date.now(), () => false), false);
    assert.equal(launcher.lockIsStale(lock, Date.now() + 11 * 60 * 1000, () => false), true);
  });

  test('owner.json is replaced whole, with no temp file left behind', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    launcher.writeOwner(lock, { pid: 1, host: 'h', time: 1 });
    launcher.writeOwner(lock, { pid: 1, host: 'h', time: 1, pgid: 42 });
    assert.deepEqual(fs.readdirSync(lock), ['owner.json']);
    assert.equal(launcher.readOwner(lock).owner.pgid, 42);
  });

  test('a torn or missing owner.json reads as no owner, and age decides', () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    assert.equal(launcher.readOwner(lock).owner, null);
    fs.writeFileSync(path.join(lock, 'owner.json'), '{"pid": 12');
    const read = launcher.readOwner(lock);
    assert.equal(read.owner, null);
    assert.equal(read.raw, '{"pid": 12');
    // Fresh directory: not stale, however unreadable its owner.
    assert.equal(launcher.lockIsStale(lock), false);
    assert.equal(launcher.lockIsStale(lock, Date.now() + 11 * 60 * 1000), true);
  });

  test('a waiter gives a not-yet-written owner.json a moment before judging by age', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    fs.mkdirSync(lock);
    // Old directory, so an immediate age judgement would call it stale.
    const old = new Date(Date.now() - 11 * 60 * 1000);
    fs.utimesSync(lock, old, old);
    setTimeout(() => launcher.writeOwner(lock, { pid: process.ppid, host: os.hostname(), time: Date.now() }), 30);
    await assert.rejects(launcher.acquireLock(lock, { waitMs: 200, pollMs: 50 }), /Timed out waiting/);
    assert.equal(launcher.readOwner(lock).owner.pid, process.ppid, 'the live owner\'s lock survived');
  });

  test('release leaves a lock that another process has taken over', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    const release = await launcher.acquireLock(lock);
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: 4242, host: os.hostname(), time: 1 }));
    release();
    assert.equal(fs.existsSync(lock), true);
  });

  test('a failure writing the lock is a clean LauncherError and leaves no lock', async () => {
    const lock = path.join(tmpdir(), 'v.lock');
    const writeFile = () => {
      throw Object.assign(new Error('ENOSPC: no space left on device'), { code: 'ENOSPC' });
    };
    await assert.rejects(
      launcher.acquireLock(lock, { writeFile }),
      (err) => err instanceof launcher.LauncherError && /Could not create the setup lock.*ENOSPC/.test(err.message),
    );
    assert.equal(fs.existsSync(lock), false);
  });
});

describe('launcher messages on stderr', () => {
  const err = (code) => Object.assign(new Error(code), { code });

  test('partial writes are completed', () => {
    const chunks = [];
    const writeSync = (fd, buf, offset, length) => {
      const n = Math.min(3, length);
      chunks.push(buf.subarray(offset, offset + n).toString());
      return n;
    };
    launcher.writeStderr('hello world\n', { writeSync });
    assert.equal(chunks.join(''), 'hello world\n');
  });

  test('EAGAIN is retried after a short sleep', () => {
    let calls = 0;
    let sleeps = 0;
    const writeSync = (fd, buf, offset, length) => {
      if (calls++ < 3) throw err('EAGAIN');
      return length;
    };
    launcher.writeStderr('x', { writeSync, sleep: () => sleeps++ });
    assert.equal(calls, 4);
    assert.equal(sleeps, 3);
  });

  test('a stderr that stays full is given up on, not thrown', () => {
    let calls = 0;
    const writeSync = () => {
      calls++;
      throw err('EAGAIN');
    };
    assert.doesNotThrow(() => launcher.writeStderr('x', { writeSync, sleep: () => {} }));
    assert.equal(calls, 101);
  });

  test('EPIPE and other errors are swallowed', () => {
    for (const code of ['EPIPE', 'EBADF', 'EIO']) {
      assert.doesNotThrow(() => launcher.writeStderr('x', { writeSync: () => { throw err(code); } }));
    }
  });
});

describe('child environment', () => {
  test('marks the launcher and adds nothing else', () => {
    const env = launcher.childEnv({ HOME: '/h' }, 'uvx');
    assert.deepEqual(env, { HOME: '/h', DEEPCTL_LAUNCHER: 'npm' });
    assert.equal(launcher.childEnv({}, 'venv').DEEPCTL_LAUNCHER, 'npm');
  });

  test('a package unpacked under npm\'s _npx cache is reported as npx', () => {
    const npx = '/home/u/.npm/_npx/0123abcd/node_modules/@deepgram/cli';
    assert.equal(launcher.childEnv({}, 'uvx', npx).DEEPCTL_LAUNCHER, 'npx');
    assert.equal(launcher.launcherKind('C:\\Users\\u\\AppData\\Local\\npm-cache\\_npx\\ab\\node_modules\\@deepgram\\cli'), 'npx');
    // How the parent was started does not matter: a global dg is npm.
    assert.equal(launcher.childEnv({ npm_command: 'exec' }, 'venv', '/usr/local/lib/node_modules/@deepgram/cli').DEEPCTL_LAUNCHER, 'npm');
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
  test('rejects an unknown DEEPCTL_NPM_RUNNER', async () => {
    await assert.rejects(launcher.resolveRunner([], { env: { DEEPCTL_NPM_RUNNER: 'pipx' }, version: '1.0.0' }), /must be one of/);
  });

  test('uses uv with the pinned spec when no venv is cached', async () => {
    const r = await launcher.resolveRunner(['--version'], {
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

  test('a cached venv wins over uv', async () => {
    const r = await launcher.resolveRunner(['x'], {
      env: { PATH: '/bin' },
      version: '1.2.3',
      platform: 'linux',
      home: '/h',
      tmpdir: '/t',
      uid: 0,
      realpath: (f) => f,
      isExecutable: (f) => f === '/bin/uv',
      exists: (f) => f.startsWith('/h/.cache/deepgram-cli/venvs/1.2.3/'),
      trustRoot: () => true,
    });
    assert.equal(r.strategy, 'venv');
    assert.equal(r.command, '/h/.cache/deepgram-cli/venvs/1.2.3/bin/deepctl');
  });

  test('DEEPCTL_NPM_RUNNER=path requires a real deepctl', async () => {
    const base = { version: '1.0.0', platform: 'linux', realpath: (f) => f };
    await assert.rejects(
      launcher.resolveRunner([], { ...base, env: { PATH: '/bin', DEEPCTL_NPM_RUNNER: 'path' }, isExecutable: () => false }),
      /no deepctl was found/,
    );
    const r = await launcher.resolveRunner(['a'], {
      ...base,
      env: { PATH: '/bin', DEEPCTL_NPM_RUNNER: 'path' },
      isExecutable: (f) => f === '/bin/deepctl',
    });
    assert.deepEqual(r, { command: '/bin/deepctl', args: ['a'], strategy: 'path' });
  });

  test('DEEPCTL_NPM_RUNNER=uvx without uv is an error', async () => {
    await assert.rejects(
      launcher.resolveRunner([], {
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

  test('a dg run from inside deepctl (nested launcher) still works', () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'uv', '#!/bin/sh\necho nested-ok\n');
    const r = runBin({ PATH: bin, HOME: home, XDG_CACHE_HOME: path.join(home, 'c'), DEEPCTL_NPM_LAUNCHER_DEPTH: '1' });
    assert.equal(r.status, 0);
    assert.equal(r.stdout, 'nested-ok\n');
  });

  test('no uv and no Python: clear error on stderr, nothing on stdout, exit 1', () => {
    const home = tmpdir();
    const r = runBin({ PATH: '/nonexistent', HOME: home, XDG_CACHE_HOME: path.join(home, 'c') });
    assert.equal(r.status, 1);
    assert.equal(r.stdout, '');
    assert.match(r.stderr, /^ERROR: The Deepgram CLI needs uv or Python 3\.10\+/);
  });

  test('passes arguments, env and exit code through a fake uv', () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'uv', '#!/bin/sh\necho "$DEEPCTL_LAUNCHER $*"\nexit 7\n');
    const r = runBin({ PATH: bin, HOME: home, XDG_CACHE_HOME: path.join(home, 'c') }, ['listen', 'a b.wav']);
    assert.equal(r.status, 7);
    const version = launcher.packageVersion(PKG_DIR);
    assert.equal(r.stdout, `npm tool run --from deepctl==${version} deepctl listen a b.wav\n`);
  });

  test('a SIGINT sent only to the launcher reaches the child after the grace period', async () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'deepctl', '#!/bin/sh\ntrap "echo got-INT; exit 130" INT\nwhile :; do sleep 0.1; done\n');
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, [BIN], {
      env: { PATH: `${bin}:/usr/bin:/bin`, HOME: home, DEEPCTL_NPM_RUNNER: 'path' },
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    await new Promise((r) => setTimeout(r, 500));
    const sentAt = Date.now();
    process.kill(child.pid, 'SIGINT');
    const code = await new Promise((r) => child.on('exit', (c) => r(c)));
    assert.equal(code, 130);
    assert.match(out, /got-INT/);
    assert.ok(Date.now() - sentAt >= 900, 'forwarded only after the grace period');
  });

  // Regression: reading process.stdin.isTTY made Node set O_NONBLOCK on a
  // piped fd 0, which deepctl inherited; a reader that had to wait for
  // input (an MCP host, `ffmpeg | dg listen`) then failed.
  const python3 = ['/usr/bin/python3', '/usr/local/bin/python3', '/opt/homebrew/bin/python3'].find((f) => fs.existsSync(f));
  test('fds 0-2 stay blocking for deepctl, and a delayed stdin writer works', { skip: !python3 && 'needs python3' }, async () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    const checker = [
      `#!${python3}`,
      'import fcntl, os, sys',
      'flags = [bool(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK) for fd in (0, 1, 2)]',
      'data = sys.stdin.read()',
      'print("nonblock=%s read=%r" % (flags, data))',
      '',
    ].join('\n');
    // A fake python so the venv strategy runs (and writes its setup line
    // to stderr) without touching PyPI: `-m venv` lays down the checker as
    // deepctl, everything else succeeds. It's installed under every name
    // the launcher probes, or a real python3.12 later on PATH (as on
    // GitHub's ubuntu runners) wins and builds a real venv.
    const fakePython = [
      '#!/bin/sh',
      'case "$1 $2" in',
      '  "-c "*) echo "(3, 12)"; exit 0;;',
      `  "-m venv") mkdir -p "$3/bin" && cp "${path.join(home, 'checker')}" "$3/bin/deepctl" && printf '#!/bin/sh\\nexit 0\\n' > "$3/bin/python" && chmod +x "$3/bin/deepctl" "$3/bin/python"; exit 0;;`,
      'esac',
      'exit 0',
      '',
    ].join('\n');
    for (const name of ['python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3']) {
      fakeExe(bin, name, fakePython);
    }
    fs.writeFileSync(path.join(home, 'checker'), checker);
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, [BIN], {
      env: { PATH: `${bin}:/usr/bin:/bin`, HOME: home, XDG_CACHE_HOME: path.join(home, 'c'), DEEPCTL_NPM_RUNNER: 'venv' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    setTimeout(() => child.stdin.end('hello\n'), 1000);
    const code = await new Promise((r) => child.on('exit', (c) => r(c)));
    assert.equal(code, 0, err);
    assert.match(err, /Setting up Deepgram CLI/);
    assert.equal(out.trim(), "nonblock=[False, False, False] read='hello\\n'");
  });

  test('re-raises the signal the child died from', () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'uv', '#!/bin/sh\nkill -TERM $$\n');
    const r = runBin({ PATH: bin, HOME: home, XDG_CACHE_HOME: path.join(home, 'c') });
    assert.equal(r.status, null);
    assert.equal(r.signal, 'SIGTERM');
    assert.doesNotMatch(r.stderr, /crashed/);
  });

  test('names the signal when the child crashes', { skip: process.platform === 'win32' && 'POSIX signals' }, () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    fakeExe(bin, 'uv', '#!/bin/sh\nkill -SEGV $$\n');
    const r = runBin({ PATH: bin, HOME: home, XDG_CACHE_HOME: path.join(home, 'c') });
    assert.equal(r.signal, 'SIGSEGV');
    assert.match(r.stderr, /^ERROR: .*uv crashed \(SIGSEGV\)\./m);
  });

  test('a venv build step killed by a signal says so', { skip: process.platform === 'win32' && 'POSIX signals' }, () => {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    const fakePython = '#!/bin/sh\ncase "$1 $2" in\n  "-c "*) echo "(3, 12)"; exit 0;;\nesac\nkill -KILL $$\n';
    for (const name of ['python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3']) fakeExe(bin, name, fakePython);
    const r = runBin({ PATH: `${bin}:/usr/bin:/bin`, HOME: home, XDG_CACHE_HOME: path.join(home, 'c'), DEEPCTL_NPM_RUNNER: 'venv' });
    assert.equal(r.status, 1);
    assert.match(r.stderr, /failed \(killed by SIGKILL\)/);
  });
});

// Regression: signal handlers used to go in only after the synchronous
// first-run setup, so a supervisor's SIGTERM during setup killed the
// launcher and orphaned the setup child (reparented to PID 1), leaving the
// lock and a half-built venv behind. Each step below hangs in a fake Python
// until the test signals the launcher.
describe('a signal during first-run setup', posixOnly, () => {
  const version = launcher.packageVersion(PKG_DIR);

  // Dead, or a zombie nobody has reaped yet (PID 1 in a container).
  function gone(pid) {
    try {
      process.kill(pid, 0);
    } catch {
      return true;
    }
    try {
      return /^\d+ \(.*\) Z/.test(fs.readFileSync(`/proc/${pid}/stat`, 'utf8'));
    } catch {
      return false;
    }
  }

  async function waitFor(predicate, what, ms = 15000) {
    const deadline = Date.now() + ms;
    while (!predicate()) {
      if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
      await new Promise((r) => setTimeout(r, 25));
    }
  }

  // Waits until the lock's owner.json names `pgid` as the setup group.
  function waitForGroup(root, pgid) {
    const file = path.join(root, `${version}.lock`, 'owner.json');
    return waitFor(() => {
      const { owner } = launcher.readOwner(path.dirname(file));
      return Boolean(owner) && owner.pgid === pgid;
    }, `owner.json to record pgid ${pgid}`);
  }

  function workspace(hangAt) {
    const home = tmpdir();
    const bin = path.join(home, 'bin');
    const pidfile = path.join(home, 'pids');
    // `hang` leaves a grandchild (sleep) under the step's shell, the way
    // pip leaves build subprocesses, and records both pids.
    const fakePython = [
      '#!/bin/sh',
      `hang() { sleep 300 & echo "$$ $!" > "${pidfile}.tmp"; mv "${pidfile}.tmp" "${pidfile}"; wait; exit 0; }`,
      'case "$1 $2" in',
      '  "-c "*) echo "(3, 12)"; exit 0;;',
      '  "-m venv") mkdir -p "$3/bin" && cp "$0" "$3/bin/python" && printf \'#!/bin/sh\\nexit 0\\n\' > "$3/bin/deepctl" && chmod +x "$3/bin/deepctl"',
      '    [ "$HANG_AT" = venv ] && hang; exit 0;;',
      '  "-m pip") case "$*" in',
      '      *" -U pip"*) [ "$HANG_AT" = pip-upgrade ] && hang;;',
      '      *) [ "$HANG_AT" = pip-install ] && hang;;',
      '    esac; exit 0;;',
      'esac',
      'exit 0',
      '',
    ].join('\n');
    for (const name of ['python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3']) fakeExe(bin, name, fakePython);
    const cache = path.join(home, 'c');
    const env = { PATH: `${bin}:/usr/bin:/bin`, HOME: home, XDG_CACHE_HOME: cache, DEEPCTL_NPM_RUNNER: 'venv', HANG_AT: hangAt };
    // Where the launcher itself will put venvs and locks: XDG_CACHE_HOME
    // on Linux, but ~/Library/Caches on macOS, which ignores it.
    const root = launcher.cacheRoots({ env, home })[0];
    return { home, root, pidfile, env };
  }

  function start(env) {
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, [BIN], { env, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (d) => (stderr += d));
    child.stdout.resume();
    const exited = new Promise((r) => child.on('exit', (code, signal) => r({ code, signal })));
    return { child, exited, stderr: () => stderr };
  }

  for (const step of ['venv', 'pip-upgrade', 'pip-install']) {
    test(`SIGTERM while the ${step} step runs: no orphan, no lock, no partial venv, dies by SIGTERM`, async () => {
      const ws = workspace(step);
      const run = start(ws.env);
      await waitFor(() => fs.existsSync(ws.pidfile), `the ${step} step to start`);
      const pids = fs.readFileSync(ws.pidfile, 'utf8').trim().split(/\s+/).map(Number);
      assert.equal(pids.length, 2);
      // The lock names the running step's process group (its shell's pid).
      // It is recorded just after spawn(), which a busy machine can let the
      // step outrun, so wait for it rather than assume it.
      await waitForGroup(ws.root, pids[0]);
      process.kill(run.child.pid, 'SIGTERM');
      const { code, signal } = await run.exited;
      // Re-raised after cleanup, so the parent sees the signal itself.
      assert.equal(signal, 'SIGTERM', run.stderr());
      assert.equal(code, null);
      assert.match(run.stderr(), /setup interrupted \(SIGTERM\)/);
      await waitFor(() => pids.every(gone), 'the setup step and its child to die', 3000);
      assert.deepEqual(fs.readdirSync(ws.root), [], 'no lock and no partial venv left');
    });
  }

  test('SIGTERM while waiting for another setup\'s lock: exits at once, foreign lock untouched', async () => {
    const ws = workspace('none');
    fs.mkdirSync(ws.root, { recursive: true, mode: 0o700 });
    const lock = path.join(ws.root, `${version}.lock`);
    liveForeignLock(lock);
    const owner = fs.readFileSync(path.join(lock, 'owner.json'), 'utf8');
    const run = start(ws.env);
    await waitFor(() => /Waiting for another dg setup/.test(run.stderr()), 'the lock wait');
    const sentAt = Date.now();
    process.kill(run.child.pid, 'SIGTERM');
    const { code, signal } = await run.exited;
    assert.equal(signal, 'SIGTERM', run.stderr());
    assert.equal(code, null);
    assert.ok(Date.now() - sentAt < 2000, 'the wait is not a blocking sleep');
    assert.deepEqual(fs.readdirSync(ws.root), [`${version}.lock`]);
    assert.equal(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8'), owner);
  });

  const linuxProc = { skip: !fs.existsSync('/proc/self/stat') && 'needs /proc' };

  test('after a SIGKILLed launcher, the next run stops the orphaned step, then builds', linuxProc, async () => {
    const ws = workspace('pip-install');
    const first = start(ws.env);
    await waitFor(() => fs.existsSync(ws.pidfile), 'the pip-install step to start');
    const pids = fs.readFileSync(ws.pidfile, 'utf8').trim().split(/\s+/).map(Number);
    await waitForGroup(ws.root, pids[0]);
    // SIGKILL cannot be handled: the step is orphaned with the lock held.
    process.kill(first.child.pid, 'SIGKILL');
    await first.exited;
    assert.ok(pids.every((pid) => !gone(pid)), 'the orphaned step is still running');
    const second = start({ ...ws.env, HANG_AT: 'none' });
    const { code } = await second.exited;
    assert.equal(code, 0, second.stderr());
    await waitFor(() => pids.every(gone), 'the orphaned step to be killed', 3000);
    assert.deepEqual(fs.readdirSync(ws.root).sort(), [version]);
  });

  test('a venv directory matches itself and paths inside it, not a prefix sibling', () => {
    const dir = path.join('/c', 'venvs', '0.4.1');
    assert.equal(launcher.mentionsDir(dir, dir), true);
    assert.equal(launcher.mentionsDir(path.join(dir, 'bin', 'python'), dir), true);
    assert.equal(launcher.mentionsDir(path.join('/c', 'venvs', '0.4.10'), dir), false);
    assert.equal(launcher.mentionsDir(path.join('/c', 'venvs', '0.4.10', 'bin', 'python'), dir), false);
    assert.equal(launcher.mentionsDir(`${dir}.lock`, dir), false);
  });

  test('a stale lock for 0.4.1 leaves a 0.4.10 setup group alone', linuxProc, async () => {
    const { spawn } = require('node:child_process');
    const root = tmpdir();
    const other = spawn('/bin/sh', ['-c', 'sleep 300; :', path.join(root, '0.4.10', 'bin', 'python')], { detached: true, stdio: 'ignore' });
    try {
      const owner = JSON.stringify({ pid: 999999, host: os.hostname(), time: 1, pgid: other.pid });
      assert.equal(launcher.killOrphanedSetup(owner, path.join(root, '0.4.1')), false);
      assert.equal(gone(other.pid), false);
    } finally {
      try {
        process.kill(-other.pid, 'SIGKILL');
      } catch {
        // Already gone.
      }
    }
  });

  test('a stale lock\'s process group is left alone unless it is building that venv', linuxProc, async () => {
    const { spawn } = require('node:child_process');
    const root = tmpdir();
    const venvDir = path.join(root, '1.0.0');
    const groups = ['elsewhere', venvDir].map((arg) => spawn('/bin/sh', ['-c', 'sleep 300; :', arg], { detached: true, stdio: 'ignore' }));
    const owner = (pgid) => JSON.stringify({ pid: 999999, host: os.hostname(), time: 1, pgid });
    try {
      // Unrelated command line (a reused pgid): not killed.
      assert.equal(launcher.killOrphanedSetup(owner(groups[0].pid), venvDir), false);
      // Another host's pgid means nothing here.
      assert.equal(launcher.killOrphanedSetup(JSON.stringify({ pid: 1, host: 'elsewhere', time: 1, pgid: groups[1].pid }), venvDir), false);
      assert.equal(gone(groups[0].pid) || gone(groups[1].pid), false);
      // Mentions the venv directory: the whole group goes.
      assert.equal(launcher.killOrphanedSetup(owner(groups[1].pid), venvDir), true);
      await waitFor(() => gone(groups[1].pid), 'the setup group to die', 3000);
      assert.equal(gone(groups[0].pid), false);
    } finally {
      for (const g of groups) {
        try {
          process.kill(-g.pid, 'SIGKILL');
        } catch {
          // Already gone.
        }
      }
    }
  });
});
