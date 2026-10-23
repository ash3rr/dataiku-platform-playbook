# Connecting Dataiku projects to external GitHub

Moving teams off Dataiku's internal project Git and onto an external GitHub organisation. DSS 14.5.1, design node on EC2 with workloads on EKS. The work went in three stages: agreeing scope with the data science and data engineering users, hands-on testing on a test server, then wiring a real project to a team repository on the production design node.

Data scientists and engineers found the internal repository unusable for review, testing and promotion, and wanted normal GitHub workflows. The original request had three parts: confirm how GitHub repositories and Dataiku are integrated (the proposal was "GitHub repo as Dataiku project library"), work out how to systematically create a GitHub repo for every new Dataiku project and link it, and define next actions.

## Four different things called "linking a repo"

When someone asks to "link a GitHub project in Dataiku", it can mean any of these, and each has a different setup path:

| Mechanism | What it does | Notes |
|---|---|---|
| Import as a project | Creates a DSS project from a repo | Only works if the repo is an exported DSS project (has the project structure committed) |
| Libraries Git reference | Pulls code from the repo into a project's library so recipes and notebooks can `import` it | Repo stays external, you sync a ref. Clean Python/R, reviews like normal code |
| Project remote | Makes the repo the `origin` of an existing DSS project, so you push, pull and branch the project itself | All the branching limits below apply here |
| Code env install | `pip install git+ssh://...` into a code env | Treats the repo as a dependency |

Plugins have their own Git integration, one repo per plugin.

The request said "GitHub repo as Dataiku project library", which is the libraries mechanism, not the project remote. Get that pinned down first, because it decides the rest of the setup. The real project we wired up later was a project remote.

## What a project remote pushes

DSS already keeps every project as a real Git repository on the backend, under `DATA_DIR/config/projects/<PROJECT_KEY>`. Adding a remote only wires `origin` to that existing repo. There is no special Dataiku layer underneath, which is why the backend command line is the escape hatch when the UI gives up.

What lands in the repo is configuration and code, never data:

* JSON config: dataset definitions (connection name, table, schema, columns), recipe settings, scenario config, dashboard layouts
* code: Python/R recipe scripts, notebook code, webapp code, library code
* plugin definitions where applicable

So "this dataset points at table X on connection Y with these columns" is in Git, but the rows are not.

The JSON is generated and verbose. A schema change made in the UI shows up as a large JSON diff in a pull request, and code recipes are stored inside JSON, so a reviewer looks at a JSON diff rather than a code diff. That is the main reason some teams push only project libraries to GitHub (plain `.py` files, reviewable) and leave project config in the internal repo. If the goal is disaster recovery or reproducibility of the whole project, pushing everything makes sense. If the goal is code review, libraries are cleaner.

## Where it is configured

Two project pages look similar and are not the same thing:

* Version Control (project dot menu, or the change-tracking indicator) is where the remote lives. With no remote configured the UI hint is subtle: click the push or pull arrow next to the branch name (near "master" and "Merge") and it prompts you to add one. Once a remote exists, Fetch, Push, Pull and Drop changes become available.
* GitHub integration (project Integrations page) is a personal-access-token link for issues and pull requests. It is not the remote. On the test server we first filled this in by mistake and nothing changed.

Admin side: Administration > Settings > Git. Each row is a group rule with these fields: Group, Allow git, URLs whitelist (a regex), Configuration options (arbitrary `git config` keys such as `core.sshCommand`), Let DSS control SSH command, and Allow per-user SSH keys.

On the test server the admin page was already fully permissive: a catch-all rule with an empty Group, Allow git ticked, the default URL whitelist regex (it matches almost any git URL, roughly `^(?:git|ssh|https?|git@...):...$`), and per-user SSH keys allowed. So any DSS user could already try to add an external remote, and only GitHub decided whether it worked.

## How authentication works

DSS shells out to the plain `git` command line client, non-interactively, as the unix account running DSS (`dataiku` on our hosts). Every question of the form "can DSS do X with Git" reduces to "can the backend's `git` authenticate with a credential that is sitting there, with no prompt". DSS natively supports two ways:

1. SSH. A system key for the `dataiku` user, per-user keys (Profile > Credentials > SSH), or keys selected per group through group rules with a `core.sshCommand` override.
2. HTTPS. The `dataiku` account must have credentials in the Git credential cache, for example `git config --global credential.helper store` plus a fine-grained PAT used as the password on first push. If the GitHub org enforces SSO/SAML, the PAT (and SSH keys) must also be authorised for the org, otherwise you get confusing permission errors.

There is no GitHub App screen anywhere in DSS. The team administering the GitHub organisation asked us not to use individual SSH keys and to "use it as an app". That is possible only by building it yourself: GitHub Apps authenticate with installation tokens that expire after about an hour, minted from the app ID and private key. You would run a git credential helper on the backend that mints the token, and DSS would use HTTPS, picking the token up from the credential cache without knowing about the exchange. On our setup that means the helper baked into the image or host, the app private key mounted as a secret, and something re-minting hourly. It is the option a security team likes (short-lived, auditable, no seat used) but on DSS it is a component the platform team would own and operate. A shared machine-user SSH key meets "no individual keys" natively, so we asked the GitHub team whether the App was a hard requirement or a preference. Not answered at the time of writing.

### System key, per-user keys and the fallback chain

* Per-user keys off: every git operation uses the `dataiku` user's key.
* Per-user keys on: if a user has set up a key in Profile > Credentials > SSH, that key is used. If not, DSS falls back to the system key.

The checkbox allows users to bring their own key but does not require it. On the test server nobody had a personal key, so everything went through the system key. In production the question is whether to force per-user keys (and remove the system key so there is no fallback) or keep a shared default.

On a containerised or pod-based backend, the instance key at `~/.ssh/id_rsa` sits on the backend filesystem and is lost on restart unless it is on a persistent volume or a mounted secret. Per-user keys are stored in DSS config and survive restarts. On our EC2 design node `/data/dataiku/.ssh/` is on the data volume, which is why the existing named keys survive.

### Deploy key, account key, machine user

Where the public key is registered on GitHub decides its reach. DSS knows nothing about it.

| Registration | Reach | Notes |
|---|---|---|
| Deploy key on a repo | That one repo only | GitHub refuses to add the same public key to a second repo. Write access must be ticked, as the default is read-only |
| Key on a personal account | Every repo that person can see | Fine for testing. Everyone pushing through DSS then acts as that person |
| Key on a machine user (bot account) | Every repo the bot account has been granted | One key, many repos. Adding a repo is a GitHub permission grant, no DSS change |

The trade-off between the first and third rows came up repeatedly. An account-level key means anyone who can trigger a push from DSS has that account's full GitHub access. Deploy keys are scoped to one repo but you need one per repo. With a monorepo the "access to other projects" concern disappears, but see the branching section for why a monorepo does not work for project remotes.

## Testing on the test server

The test DSS server had several old keys in `~/.ssh` of the `dataiku` user, and earlier `ssh -T git@github.com` from another user had returned a greeting naming a specific project repo, which suggested a deploy key. On the test server itself:

```
[dataiku@<host> ~]$ ssh -T git@github.com
The authenticity of host 'github.com (...)' can't be established.
ED25519 key fingerprint is SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU.
Are you sure you want to continue connecting (yes/no/[fingerprint])? yes
Warning: Permanently added 'github.com' (ED25519) to the list of known hosts.
git@github.com: Permission denied (publickey).
```

So none of the keys were registered. We generated a fresh one:

```bash
ssh-keygen -t ed25519 -C "dataiku-test"
cat ~/.ssh/id_ed25519.pub
```

and registered it on a personal GitHub account rather than as a deploy key on the test repo. `ssh -T git@github.com` then greeted that personal account. That was acceptable for testing but meant every push from the test DSS would authenticate as one person and could reach anything that person could see.

Test plan we used (on a throwaway project, because adding a remote or switching branch on a shared project affects every collaborator):

1. Create an empty GitHub repo (no README, no .gitignore).
2. Sort out auth on the server as above, or use HTTPS with a fine-grained PAT and `credential.helper store`.
3. Check the admin Git rule allows the URL (it already did).
4. Create a test project with a couple of dummy Python recipes.
5. Version Control > add remote > push.
6. Look at what landed in GitHub, then branch, change, push, open a PR, merge on GitHub, pull back.

Things to observe during the test: what the JSON looks like, whether a PR is reviewable, the branch-switch-affects-everyone behaviour, libraries versus full project, and SSH versus PAT.

## Wiring a real project

The goal was to make a team GitHub repository (`git@github.com:<GITHUB_ORG>/<REPO>.git`) the remote for a DSS project on the production design node.

### Existing convention on the instance

Two existing group rules showed how others had done it. Each rule:

* applies to one dedicated DSS group per use case (for example `<usecase_group_a>`, `<usecase_group_b>`),
* keeps the default broad URL whitelist,
* overrides `core.sshCommand` to pin one named key file, for example `ssh -i /data/dataiku/.ssh/id_rsa-github-<org>-<usecase> -o StrictHostKeyChecking=yes`,
* has "Let DSS control SSH command" unchecked, which is correct: the help text says to disable it when you override `core.sshCommand` yourself, otherwise DSS appends its own flags and can fight the override.

Other groups seen in the rule list followed the same per-application naming. Broad whitelist plus per-use-case key name plus group-as-scope is the fingerprint of a deploy-key-per-repo convention: one group, one rule, one key, one repo. In that model the whitelist does no repo pinning at all, and the deploy key is the only thing stopping a group from reaching other repos. If anyone ever swapped such a key for a machine-user key, the broad regex would suddenly let that group push anywhere the bot can see. So the safety comes from the key type, and the DSS config adds none. We did not confirm with the rule owners whether the existing keys are deploy keys.

### How group rules pick a key

We checked the documentation because two readings were possible. DSS evaluates rules first-match: the first rule that matches both the requesting user's group membership and the remote URL is applied. If the URL does not match a rule's whitelist, the rule is shown as skipped and evaluation falls through to the next rule. If no rule matches, Git access is denied.

Consequences:

* The rule binds to the people (the group), not to a project. Every project a group member touches funnels through the same rule.
* The same group can appear in several rules, each narrowed to one repo with its own key. Push to repo A matches rule A, and push to repo B skips A and matches B. This is the documented pattern for remotes that control access by which key pushes.
* Narrow rules must sit above any broad rule for the same group, otherwise the broad rule wins.
* The DSS project URL is not what the whitelist is tested against. We nearly put the DSS project URL into the rule, but the regex is tested against the git remote string (`git@github.com:...`).

A narrow whitelist for one repo looks like `^git@github\.com:<GITHUB_ORG>/<REPO>(\.git)?$`. Test it against the exact remote string before saving. The trailing `.git` is the usual mismatch.

### Designs considered

| Option | How | Cost | Outcome |
|---|---|---|---|
| Follow the house convention | New DSS group per use case, broad whitelist, one deploy key per repo | Group sprawl, a new group per repo | Recommended. One row tells you group, key and repo, easy to audit and offboard |
| Same group, several narrow rules | Repeat the group, one rule and one deploy key per repo, narrow regex, order matters | N repos means N rules and N keys, each a DSS admin change | Valid, not the house style |
| Machine user | One bot account, one key, one rule, with repos added by GitHub grant | Broad blast radius by design, needs a bot seat | Best if a team has many or growing repos |
| Monorepo | One repo for many projects | Does not work for project remotes, see below | Rejected for project remotes |
| GitHub App | Credential helper minting tokens, HTTPS | Platform team builds and runs it | Parked pending the GitHub team's answer |

### Project access is not push access

The project had been given broad access for the whole data engineering and data science team group. Reusing that same broad group in the Git rule would have let every member push to the repo the moment they added the remote, and their push rights would be invisible in any offboarding review because they are bundled into a generic group. Project permissions (Project > Security) and the Git rule's group are set in different places and can be different groups. A user with write on the project who is not in the rule's group gets denied on push, and the rule grants no project access by itself.

The conclusion we reached: ask "who should be allowed to push to this repo" (names or a role, not "the team"), make that a dedicated group named in the house style, and mint a key for it. In the end we went a different way for now: a single reusable "data team" key, to be revisited if the team later decides on per-repo keys.

### What we built

Key generated on the design node backend as `dataiku`, no passphrase (non-interactive git cannot answer a passphrase prompt, so `-N ""` is required, and the private key is protected with file permissions):

```bash
ssh-keygen -t ed25519 -C "dss-github-<org>-datateam" -f /data/dataiku/.ssh/id_rsa_github_<org>_datateam -N ""
chmod 600 /data/dataiku/.ssh/id_rsa_github_<org>_datateam
chown dataiku:dataiku /data/dataiku/.ssh/id_rsa_github_<org>_datateam
```

Verified:

```
$ ls -l /data/dataiku/.ssh/id_rsa_github_<org>_datateam*
-rw------- 1 dataiku dataiku 419 <date> <time> /data/dataiku/.ssh/id_rsa_github_<org>_datateam
-rw-r--r-- 1 dataiku dataiku 105 <date> <time> /data/dataiku/.ssh/id_rsa_github_<org>_datateam.pub
```

419 and 105 bytes are right for an ed25519 pair. The files are named `id_rsa_...` by convention even though the key is not RSA. Existing keys on the box mix hyphens and underscores (`id_rsa-github-...`), which ssh does not care about. The only rule is that the `ssh-keygen -f` path, the file on disk and the `-i` path in the rule are byte-for-byte the same string. Copy the name from `ls` rather than retyping it. A wrong path fails in a way that looks like a permissions or host-key problem.

Group rule:

* Group: the group whose members will push
* Allow git: ticked
* URLs whitelist: because this is a reusable key, the whitelist is now the only repo fence inside DSS, so scope it to the one repo for now (`^git@github\.com:<GITHUB_ORG>/<REPO>(\.git)?$`) rather than the broad default
* Configuration options: `core.sshCommand` = `ssh -i /data/dataiku/.ssh/id_rsa_github_<org>_datateam -o StrictHostKeyChecking=yes`
* Let DSS control SSH command: unchecked
* Position: above any broad rule for the same group

Remote URL (SSH form, because the HTTPS form would ignore the key and ask for credentials DSS cannot supply):

```
git@github.com:<GITHUB_ORG>/<REPO>.git
```

Use Fetch first, not push. It proves the chain read-only before writing history.

### The error we hit

```
An error happened while adding the following remote: origin (git@github.com:<GITHUB_ORG>/<REPO>.git),
and then fetching it, caused by: UnauthorizedException: No Git configuration group rule matched for
auth=<AC:user:<user login>>, remoteURL=git@github.com:<GITHUB_ORG>/<REPO>.git, Git access denied
```

DSS rejected it before SSH ran, so key, GitHub registration and host key were not yet in play. Two candidates: the user running the action is not in the rule's group, or the regex does not match the URL. Fastest triage is to set the rule's whitelist back to the broad default temporarily: if it then works, the regex was wrong, and if it still denies, it is group membership.

In our case it was membership. The account setting it up was not in the group named in the rule. Because DSS picks the key by the groups of whoever runs the action, everyone who fetches or pushes through DSS must be in the group, including the platform engineer wiring it. If that person should not have standing push rights, add them temporarily and remove afterwards, or have a real member run the first Fetch.

### Remaining steps (open at the time of writing)

1. Add the setting-up account to the rule's group and retry Fetch.
2. Register the `.pub` on the bot/machine-user account's SSH keys (not as a deploy key on the repo, because this is the reusable route), and make sure that account has write on the repo.
3. Confirm `github.com` is in `/data/dataiku/.ssh/known_hosts`. With `StrictHostKeyChecking=yes` there is no prompt, so a missing host key fails hard. Check with `ssh -i /data/dataiku/.ssh/id_rsa_github_<org>_datateam -T git@github.com`: you want a greeting naming the bot account, or a permission error, not "Host key verification failed". If needed, `ssh-keyscan github.com >> /data/dataiku/.ssh/known_hosts` and compare the fingerprint with GitHub's published one.

Whether the first Fetch succeeded after these steps is not recorded.

### What "change it later" means

Moving from the shared key to per-repo deploy keys later is not a rename. It means generating new keys, registering each as a deploy key on its repo, removing the old key from the bot account, and editing or adding DSS rules. Deferring it is cheap, but the change later still costs work.

Handoffs needed for any of this: backend shell access on the design node (key generation), a GitHub org or repo admin (key registration), and a DSS admin (group and rule).

## Landmines on the project remote

* Host key seeding. DSS runs `git` non-interactively, so `github.com` must already be in `known_hosts` for the `dataiku` user. On an ephemeral backend filesystem that has to be baked into the image or a mounted config, not done by hand once.
* First push rejected on a non-empty repo. If the GitHub repo has even a README, the project's local history and the remote's are unrelated. DSS pull is `pull --rebase` and will not bridge unrelated histories from the UI. Start from a truly empty repo, or reconcile once by hand in the project's repo on the backend.
* Conflicts cannot be resolved in DSS. Pull does a rebase and aborts on conflict. The documented workaround is to resolve the merge with an external Git tool, then switch back in DSS and drop local changes.
* One repo maps to one project at its root. Project remotes are repo-per-project regardless of any monorepo ambition.

## Branching

A DSS project is checked out on one branch at a time for everyone. Switching branch on a project switches it for every collaborator. You cannot work on several branches at once. Dataiku's recommendation is to tick "duplicate the project" when creating a branch, so work happens on a copy. In practice branching means duplicating whole projects.

Merging is not available inside DSS. The workflow is: branch in DSS, push, open a PR on GitHub, merge on GitHub, pull back into DSS. Two more documented points:

* Merge commits make it impossible to revert a project to an earlier state from the DSS UI. Use squash merging (only available in an external tool such as GitHub) if you want revert to keep working.
* Git's text merge does not understand DSS's interdependent JSON (recipe metadata, scenario config, connection references). A merge can leave corrupted or broken objects and an inconsistent flow, because the conflict resolution has no view of whether recipes are still valid.

Community feedback says the same thing bluntly: code and metadata live together in JSON, so merging externally means resolving conflicts in generated JSON. One community post described a `checkout_and_pull_with_conflict_resolution` function built on GitPython and run from CI/CD to sync offline development before tests. Someone also remembered a Reddit thread about an engineer at a large tech company writing a macro or script to work around a Dataiku Git issue. We could not find it or confirm what it solved. Other teams were said to use "a macro" to resolve merge issues. What it does (prevent conflicts or resolve them afterwards) was never established.

Options we wrote down for the team discussion:

1. Single main branch, one instance per environment. Branch in Git for feature work, but only merge back to main when ready for the whole instance, and promote by bundle. Cleanest, least flexible.
2. Monorepo with one folder per project. Branches touching different projects would not collide. Correction: this only works for shared library code via Libraries > Git references. For project remotes it does not work, because DSS versions one project to one repo at the repo root and several projects' histories would collide. We first listed it as viable and later struck it.
3. Git as audit log only. Developers work in DSS without branching, push for history, and promotion goes through DSS export/import or bundles. Slower, avoids merge problems.

The question that decides between them: is the pain people stepping on each other on one instance (a development workflow problem), or getting changes from dev to prod safely (a promotion problem)? Not answered when the work stopped.

If only project libraries go to GitHub, most of these problems disappear. Libraries are plain Python/R and branch and merge like any other code.

## Production checklist (not yet applied)

* Narrow the catch-all whitelist to the organisation, for example `^git@github\.com:<GITHUB_ORG>/.*$`, so people cannot attach random external repos.
* Decide per-user keys versus shared keys, and whether to remove the system key fallback.
* Decide who creates repos (manual or an org template) and who links them in DSS (admins only or project creators).
* Make the team aware that merges happen on GitHub and that branch switches affect everyone.

## Open questions

* GitHub App versus machine-user key: hard requirement or preference for the GitHub team.
* Whether the existing named keys on the instance are deploy keys or machine-user keys.
* Libraries-only versus full project remotes as the standard.
* The development versus promotion question above.
* Whether the first Fetch on the real project succeeded after the group fix.
