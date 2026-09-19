# tencent-repos

`tencent-repos` is a static catalog of active repositories from Tencent's
`Tencent` GitHub organization. It groups public repositories by purpose and
keeps GitHub metadata fresh with a scheduled update script.

Included repositories match this filter:

- GitHub org: `Tencent`
- More than 200 stars
- Non-fork and non-archived
- Last pushed within the previous three calendar months

The page is tuned to surface repositories with both recent traction and broad
audience: the lead table blends recent commits, freshness, stars, and forks,
while each technology cluster shows every qualifying repository sorted by stars.

The seven clusters cover AI and agents, WeChat/mobile runtimes, TDesign and
mini programs, cloud and distributed data systems, JDKs and developer tools,
graphics and games, and security/reliability.

## Open locally

Open `index.html` directly in a browser, or run a local preview server:

```bash
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.

## Update repository metadata

```bash
python3 update_stats.py
```

The script fetches stars, forks, total commits, recent commits, last commit
dates, primary language, topics, and descriptions from GitHub. It rewrites the
fresh-traction table, the cluster tables, and `stats_history.json`.

For local runs, authenticate with the GitHub CLI or set `GITHUB_TOKEN`:

```bash
gh auth login
python3 update_stats.py
```

## Main files

- `index.html` - generated static catalog page.
- `update_stats.py` - GitHub metadata fetcher and HTML generator.
- `stats_history.json` - stored snapshots used for 30-day star and fork deltas.
- `favicon.svg` - Tencent-themed favicon.
- `.github/workflows/static.yml` - deploys the static site to GitHub Pages.
- `.github/workflows/update-stats.yml` - scheduled catalog refresh.
- `cloudflare/worker.js` - proxies the site at `https://glaubi.net/tencent`.
