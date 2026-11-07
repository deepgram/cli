// Offline release-please dry run.
//
// Runs release-please's own Manifest (the code release.yml's action runs)
// against a local git repository, with no GitHub API: commits, tags and file
// contents come from git, the previous releases come from RELEASES (JSON), and
// there are no open pull requests. It builds the release PR exactly as the
// action would, then writes the PR's file changes into the working tree so
// the caller can diff them.
//
// Usage: node dry_run.js REPO_DIR OUT_JSON
//   RELEASES='[{"tagName":"v0.4.0","sha":"<sha>"}]'
//   NODE_PATH must resolve `release-please` (pinned to the version the
//   pinned release-please-action bundles).
'use strict';
const fs = require('fs');
const path = require('path');
const {Manifest} = require('release-please');
const {LocalGitHub} = require('release-please/build/src/local-github');

async function main() {
  const [repoDir, outJson] = process.argv.slice(2);
  const releases = JSON.parse(process.env.RELEASES || '[]');
  const repository = {owner: 'deepgram', repo: 'cli', defaultBranch: 'main'};
  const offlineApi = {
    repository,
    async *pullRequestIterator() {},
    async *releaseIterator() {
      let id = 1;
      for (const r of releases) {
        yield {
          id: id++,
          name: r.tagName,
          tagName: r.tagName,
          sha: r.sha,
          notes: '',
          url: `https://example.invalid/${r.tagName}`,
          draft: false,
        };
      }
    },
  };
  const github = new LocalGitHub(repository, offlineApi, repoDir);
  const manifest = await Manifest.fromManifest(
    github,
    'main',
    '.github/release-please-config.json',
    '.github/.release-please-manifest.json'
  );
  const prs = await manifest.buildPullRequests();
  const out = [];
  for (const pr of prs) {
    const changes = await github.buildChangeSet(pr.updates, 'main');
    for (const [file, change] of changes) {
      fs.writeFileSync(path.join(repoDir, file), change.content);
    }
    out.push({
      title: pr.title.toString(),
      headRefName: pr.headRefName,
      releases: pr.body.releaseData.map(r => ({
        component: r.component || '',
        version: r.version ? r.version.toString() : '',
      })),
      files: [...changes.keys()].sort(),
    });
  }
  fs.writeFileSync(outJson, JSON.stringify(out, null, 2) + '\n');
  console.log(JSON.stringify(out.map(p => ({title: p.title, releases: p.releases})), null, 2));
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
