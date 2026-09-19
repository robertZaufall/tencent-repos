# AGENTS.md

This file is the working guide for agents editing `tencent-repos`.

## What this repo is

`tencent-repos` is a single-page static catalog of public repositories from
Tencent's `Tencent` GitHub organization. It follows a
lightweight static-project shape: no framework, no package manager, and no
build step.

The page is generated from GitHub metadata by `update_stats.py`.

## First rule: preserve generated ownership

Most changes to repository rows, cluster contents, timestamps, and stats should
be made in `update_stats.py`, then regenerated with:

```bash
python3 update_stats.py
```

Avoid hand-editing generated table rows in `index.html`; those edits will be
overwritten by the next scheduled refresh.

## Repo layout

- `index.html`: generated static page with embedded CSS and JavaScript.
- `update_stats.py`: fetches GitHub metadata, assigns clusters, ranks tables,
  rewrites `index.html`, and updates `stats_history.json`.
- `stats_history.json`: lightweight history used for 30-day star and fork
  deltas. Keep it committed so scheduled runs can compute traction.
- `favicon.svg`: static favicon.
- `.github/workflows/static.yml`: deploys to GitHub Pages.
- `.github/workflows/update-stats.yml`: scheduled refresh and deploy.
- `cloudflare/worker.js`: proxy used for
  `https://glaubi.net/tencent`.

## Catalog rules

Included repositories must be:

- Owned by the `Tencent` GitHub org.
- Non-forks and non-archived.
- More than 200 stars.
- Pushed within the last three calendar months.

The page contains seven rough technology clusters:

1. AI, Agents & Models
2. Mobile, WeChat & Cross-Platform Runtime
3. Design Systems, Web & Mini Programs
4. Cloud, Databases & Distributed Systems
5. Languages, JDKs & Developer Tools
6. Graphics, Games & Media
7. Security, Red Teaming & Reliability

Each cluster table shows every qualifying repository in that cluster by stars.

The first table is the traction table. It shows the top 25 repositories ranked
by recent commit activity, freshness, stars, and forks. Once a 30-day history
baseline exists, stored star and fork deltas are added to the score.

## Rebuilding the catalog

Run a full rebuild whenever you want to refresh the qualifying repository set
and recluster the catalog:

```bash
python3 update_stats.py
```

That command queries GitHub for current `Tencent` repositories
matching the catalog rules, fetches metadata and recent commit counts, assigns
each repo to a rough technology cluster, rebuilds the fresh-traction table,
rebuilds the cluster tables, rewrites `index.html`, and updates
`stats_history.json`.

Commit both generated files after a rebuild:

```bash
git add index.html stats_history.json
```

If you change cluster definitions, scoring, columns, or table order, edit
`update_stats.py` first and regenerate rather than editing `index.html` by hand.

## GitHub Actions

The scheduled workflow writes directly to the default branch when metadata
changes. Before starting manual work in an existing checkout, inspect status and
pull safely:

```bash
git status --short --branch
git fetch origin
git pull --ff-only origin main
```

If the worktree is dirty, do not discard local changes you did not make.

## Custom-domain deployment

The canonical URL is `https://glaubi.net/tencent/`. Deploy from `cloudflare/`
with `wrangler deploy --dry-run`, then `wrangler deploy`. The exact bare path
redirects to the slash URL; the descendant route proxies this repository's Pages
origin. Use the slash URL for query strings (Cloudflare exact routes include the
query string in matching). Do not broaden the route to `/tencent*`.
Verify the slash page, `favicon.svg`, missing-path 404, and `wrangler deployments
status --json`. The public route-index card lives separately in `~/git/glaubinet`.
