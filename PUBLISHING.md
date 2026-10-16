# Publishing Argus

The GitHub repository is the marketplace. Publishing means merging the tested
plugin changes into the branch that users installed from (normally `main`).
A GitHub Release, tag, package upload and manual manifest version bump are not
required. Keep `version` absent from both manifests: updates track Git commits.

## Publish the current patch

From the Argus checkout in PowerShell:

```powershell
Set-Location D:\GitHub\Argus
git switch -c fix/argus-audit-release
git add .github/workflows/ci.yml AGENTS.md ARGUS_CAPABILITIES.md README.md PUBLISHING.md plugins/argus tests
git diff --cached --stat
git commit -m "Fix plugin updates, audit evidence and profile stall counting"
git push -u origin fix/argus-audit-release
```

Review the staged summary before committing. The paths above include the new
implementation, guides, fixtures and regression tests. They exclude `.audit/`,
the cloned test repositories and the supplied raw comparison artifacts.

On GitHub, open a pull request from `fix/argus-audit-release` into `main`.
Wait for the Windows, macOS and Linux core CI jobs, then merge. That merge
publishes the plugin for users following the repository's default branch.
Users pinned to a tag/commit must deliberately change their marketplace pin.

## Update the test machine

Run in its ordinary terminal, outside an active Claude conversation:

```sh
claude plugin marketplace update argus
claude plugin update argus@argus
claude plugin list
```

Start a new Claude Code session. Existing sessions retain their loaded skills
and MCP processes. No cache deletion or forced process termination is needed.
If your marketplace was added as a local directory, update that checkout first.
For a new machine, register and install it:

```sh
claude plugin marketplace add Overproness/Argus
claude plugin install argus@argus
```

Call the `audit_status` MCP tool in the new session. Its `plugin_root` should
identify the installed copy and `restart_required` should be false. Compare its
payload hash with `python plugins/argus/scripts/auditor_cli.py doctor` from a
matching checkout on that machine. Hashes identify exact file bytes, including
line endings. A missing `audit_status` tool means the session still uses an old
plugin. For development before publishing, use `claude --plugin-dir plugins/argus`.

References: [Claude Code version resolution](https://code.claude.com/docs/en/plugins/loading#how-claude-code-computes-the-version)
and [plugin updates](https://code.claude.com/docs/en/discover-plugins#update-plugins-now).
