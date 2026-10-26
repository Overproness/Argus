# Publishing Argus

Argus has two distribution formats: the complete Claude Code plugin in
`plugins/argus`, and a free setup and audit skill in `skills/argus-audit` that
points users to the full tools. Publishing the GitHub
marketplace does not submit a listing to Anthropic or Agensi.

## Public source and local material

Track implementation, plugin manifests, workflow skills, regression tests,
test fixtures, CI, and maintained documentation. Keep raw audits, conversations,
application snapshots, private judges, generated ZIPs, local plans and launch
drafts outside Git tracking. `.gitignore` covers those local paths; it does not
untrack a file that was previously committed.

Before a release, inspect both `git status --short` and `git diff --cached`.
Use explicit paths when staging. Confirm that the staged tree contains no
gitlinks to local test repositories, credentials, generated reports or private
evaluation material. Keep any preservation archives local too.

Removing files from a new commit does not erase earlier commits. Data already
published in history or downloaded by other people cannot be made secret by
adding an ignore rule. Treat exposed benchmark answers as development data.

## Release through the GitHub marketplace

Run from the repository root in your development environment:

```sh
python -m pytest tests -q
git diff --check
git switch -c release/describe-your-change
git add README.md PUBLISHING.md AGENTS.md ARGUS_CAPABILITIES.md .gitignore
git add .claude-plugin .github plugins skills tests action.yml
git diff --cached --stat
git diff --cached
git commit -m "Describe the tested change"
git push -u origin release/describe-your-change
```

These are example paths: stage only files belonging to your release. Ignored
local material stays out of a normal `git add`; do not override that with `-f`.
If a cleanup includes staged removals made with `git rm --cached`, review and
include those removals in the same commit.

Open a pull request into `main`, wait for Linux, macOS and Windows CI, and merge.
The merge updates the plugin for people following that branch. Users pinned to
a tag or commit must change their pin to receive a later release.

This Git-hosted marketplace uses commit-based versions. Keep `version` absent
from the plugin and marketplace manifests. If you choose explicit versions in
the future, increment them for every release; reusing a version string can leave
users on an old cached payload. A GitHub Release or tag is optional for this
distribution. See [Claude Code's publishing guide](https://code.claude.com/docs/en/plugins/publish).

## Update an installed copy

Run in a terminal, then start a fresh Claude Code session:

```sh
claude plugin marketplace update argus
claude plugin update argus@argus
```

For a first install:

```sh
claude plugin marketplace add Overproness/Argus
claude plugin install argus@argus
```

Call MCP `audit_status`. Compare its `plugin_root` and payload hash with CLI
`doctor` from the intended checkout. Restart if `restart_required` is true.
A fresh CLI process cannot identify the payload of an already-running MCP
server. Use `claude --plugin-dir ./plugins/argus` for uncommitted development.

## Submit the free setup listing to Agensi

Agensi delivers skill instructions and text through its own MCP service. It
does not install Claude Code plugins, hooks or MCP settings, or host Argus's
server. Submit the setup skill at `skills/argus-audit/SKILL.md`, rather than a copy
of the complete plugin with its Python modules. It explains how to install the
full plugin or configure its MCP server; audits then use the actual Argus tools.
The user's agent needs local file and process access. State these requirements
in the listing, and choose **Free** pricing.

Source: [What you can sell](https://www.agensi.io/docs/what-you-can-sell) and
[how agents load skills](https://www.agensi.io/docs/how-agents-load-skills).

1. Create an Agensi account, enable **Creator Mode** under Dashboard > Settings,
   and complete your creator profile. Free listings do not need a payout method;
   paid listings do. See the [creator quickstart](https://www.agensi.io/docs/creator-quickstart).
2. Create a ZIP containing just the maintained workflow. From the repository
   root, this Python command writes a local archive:

   ```sh
   python -c "from pathlib import Path; from zipfile import ZipFile, ZIP_DEFLATED; p=Path('.local/launch'); p.mkdir(parents=True, exist_ok=True); z=ZipFile(p/'argus-audit-agensi.zip', 'w', ZIP_DEFLATED); z.write('skills/argus-audit/SKILL.md', 'SKILL.md'); z.close()"
   ```

   The archive has `SKILL.md` at its root. Do not include local dependencies, audit
   outputs or private benchmark materials. See [packaging requirements](https://www.agensi.io/docs/packaging).
3. Open [the submission form](https://www.agensi.io/dashboard/submit), upload the
   ZIP, and review the parsed listing. Suggested name: **Argus Audit**. The form
   checks whether the name is available.
4. Fill in the summary and Markdown description. Explain the map > queue >
   reproduction > report workflow, the separate Argus installation, supported
   environments, output files, permissions, and limits. Set **Needs access to**
   to the separately configured Argus MCP server. Link the repository README as
   documentation. Select relevant categories such as Code Quality & Review
   and Testing & Debugging, and choose **Free**. No payout setup is needed.
5. State compatibility accurately: a coding agent with local filesystem and
   process access, preconfigured Argus MCP tools, Python 3.12+ for the full
   Argus workflow, and the target application's runtimes/dependencies. Loading
   the skill in ordinary chat alone does not supply those capabilities.
6. Add an actual request and its complete observed result in **See it in
   action**. Show real evidence from a permitted demo; do not claim a local
   schema check proves that Agensi delivery works. Review permission and data
   handling disclosures, then submit for review yourself.
7. Resolve scan/reviewer feedback. Once approved, run the delivered skill through
   Agensi with your own agent and verify the missing-tool behavior and one real
   audit before advertising compatibility broadly.

The website currently accepts ZIPs up to 50 MB and names up to 60 characters;
summaries are limited to 300 characters. A square logo and screenshots can
illustrate the workflow. The current fields are documented in
[Submit on the website](https://www.agensi.io/docs/submit-on-the-website).

The repository currently declares no software license. Choose and document the
terms you intend to grant before describing the project as open source or
selling redistribution rights. This guide does not choose a license for you.

## Anthropic's directory and official marketplace

Anthropic's directory has a separate [developer portal](https://claude.ai/directory/manage)
and [submission procedure](https://claude.com/docs/plugins/submit). Submit the
repository with plugin path `plugins/argus`. That folder needs its own README
and a declared license before directory submission; the root README alone is
insufficient. Check the current [directory checklist](https://claude.com/docs/plugins/pre-submission-checklist).

The directory portal does not submit to `claude-plugins-official`. Anthropic's
[publishing documentation](https://code.claude.com/docs/en/plugins/publish#submit-to-anthropics-directory)
says to discuss that listing with an Anthropic partner contact if you have one.
Do not describe the GitHub marketplace or an Agensi listing as official
Anthropic approval.
