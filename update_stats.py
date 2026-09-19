#!/usr/bin/env python3
"""
Build and refresh the Tencent GitHub repository catalog.

The script fetches public GitHub metadata for repositories in Tencent's
Tencent organization that were updated in the last three months and have more
than 200 stars, groups them into technology clusters, and rewrites index.html.

Requirements:
  - Python 3.10+
  - A GitHub token from GITHUB_TOKEN, GH_TOKEN, or an authenticated gh CLI

Usage:
  python3 update_stats.py
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ORG = "Tencent"
MIN_STARS = 201
TOP_PER_CLUSTER = 0
TRACTION_DAYS = 30
HISTORY_DAYS = 140
GITHUB_API = "https://api.github.com"
USER_AGENT = "tencent-repos-catalog"
AUTO_KEYWORD_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("WeChat", ("wechat", "weixin", "微信")),
    ("TDesign", ("tdesign",)),
    ("AI", ("artificial intelligence", "machine learning", "deep learning")),
    ("Agents", ("agent", "agents", "agentic")),
    ("LLM", ("large language model", "llm", "language model")),
    ("RAG", ("rag", "retrieval")),
    ("Inference", ("inference", "ncnn", "tnn")),
    ("Android", ("android",)),
    ("iOS", ("ios",)),
    ("Mini Program", ("miniprogram", "mini program", "小程序")),
    ("Vue", ("vue",)),
    ("React", ("react",)),
    ("Flutter", ("flutter",)),
    ("Redis", ("redis", "tendis")),
    ("Kubernetes", ("kubernetes", "k8s")),
    ("JDK", ("jdk", "openjdk", "kona")),
    ("QUIC", ("quic",)),
    ("MCP", ("mcp", "model context protocol")),
    ("Security", ("security", "red team", "jailbreak")),
    ("Unity", ("unity",)),
    ("Unreal", ("unreal", "ue4", "ue5")),
)
SUBSTRING_KEYWORDS = {"WeChat", "TDesign", "AI", "LLM", "RAG", "MCP", "JDK", "QUIC"}


@dataclass(frozen=True)
class Cluster:
    key: str
    name: str
    summary: str
    accent: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class SourceSection:
    key: str
    name: str
    summary: str
    query: str
    accent: str


CLUSTERS: tuple[Cluster, ...] = (
    Cluster(
        "ai-agents-models",
        "AI, Agents & Models",
        "Inference engines, RAG platforms, agent CLIs, multimodal embeddings, and applied model tooling.",
        "blue",
        (
            "agent",
            "embedding",
            "inference",
            "language model",
            "llm",
            "machine learning",
            "ncnn",
            "neural",
            "rag",
            "transformer",
        ),
    ),
    Cluster(
        "mobile-wechat-runtime",
        "Mobile, WeChat & Cross-Platform Runtime",
        "WeChat-origin storage, hot-fix, Hippy, PuerTS, and other mobile/cross-platform runtimes.",
        "cyan",
        (
            "android",
            "hotfix",
            "ios",
            "mmkv",
            "mobile",
            "tinker",
            "wechat",
            "weixin",
        ),
    ),
    Cluster(
        "design-web-miniprograms",
        "Design Systems, Web & Mini Programs",
        "TDesign component libraries, micro-frontends, markdown editors, and WeChat mini program UI.",
        "purple",
        (
            "component",
            "design",
            "frontend",
            "miniprogram",
            "react",
            "tdesign",
            "vue",
            "web",
        ),
    ),
    Cluster(
        "cloud-data-distributed",
        "Cloud, Databases & Distributed Systems",
        "Spring Cloud Tencent, Tendis, RPC frameworks, service discovery, and distributed storage.",
        "green",
        (
            "cloud",
            "database",
            "distributed",
            "kubernetes",
            "redis",
            "rpc",
            "spring",
            "storage",
            "tendis",
        ),
    ),
    Cluster(
        "languages-jdks-devtools",
        "Languages, JDKs & Developer Tools",
        "Tencent Kona JDKs, PHP frameworks, logging, profilers, and workflow CLIs.",
        "yellow",
        (
            "compiler",
            "jdk",
            "kona",
            "logging",
            "openjdk",
            "php",
            "profiler",
            "tool",
        ),
    ),
    Cluster(
        "graphics-games-media",
        "Graphics, Games & Media",
        "PAG animation, GPU 2D graphics, Unreal/Unity scripting, digital humans, and simulation.",
        "orange",
        (
            "animation",
            "game",
            "graphics",
            "pag",
            "rendering",
            "unreal",
            "unity",
            "video",
        ),
    ),
    Cluster(
        "security-reliability",
        "Security, Red Teaming & Reliability",
        "AI red-team platforms, vulnerability benchmarks, SM crypto providers, and production reliability.",
        "red",
        (
            "crypto",
            "jailbreak",
            "red team",
            "security",
            "sm2",
            "vulnerability",
        ),
    ),
)


EXTRA_SECTIONS: tuple[SourceSection, ...] = ()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Tencent GitHub repository catalog.")
    parser.add_argument("--file", default="index.html", help="HTML file to write")
    parser.add_argument("--history", default="stats_history.json", help="history JSON file")
    parser.add_argument("--org", default=ORG, help="GitHub organization")
    parser.add_argument("--min-stars", type=int, default=MIN_STARS, help="minimum stars")
    parser.add_argument("--top-per-cluster", type=int, default=TOP_PER_CLUSTER, help="cluster row limit; 0 shows all")
    parser.add_argument("--traction-days", type=int, default=TRACTION_DAYS)
    parser.add_argument("--months", type=int, default=3, help="recency window in calendar months")
    parser.add_argument("--skip-commit-counts", action="store_true", help="skip per-repo commit counts")
    return parser.parse_args()


def subtract_months(dt: datetime, months: int) -> datetime:
    month = dt.month - months
    year = dt.year
    while month <= 0:
        month += 12
        year -= 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return dt.replace(year=year, month=month, day=min(dt.day, days_in_month[month - 1]))


def get_token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        token = result.stdout.strip()
        return token or None
    return None


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self.token = token

    def request(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = Request(url, headers=headers)
        for attempt in range(4):
            try:
                with urlopen(req, timeout=60) as response:
                    body = response.read().decode("utf-8")
                    response_headers = {k.lower(): v for k, v in response.headers.items()}
                    return json.loads(body), response_headers
            except HTTPError as exc:
                if exc.code in (403, 429) and attempt < 3:
                    reset = exc.headers.get("X-RateLimit-Reset")
                    if reset and reset.isdigit():
                        delay = max(2, min(60, int(reset) - int(time.time()) + 2))
                    else:
                        delay = 3 * (attempt + 1)
                    time.sleep(delay)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"GitHub API failed for {url}: HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError) as exc:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"GitHub API failed for {url}: {exc}") from exc
        raise RuntimeError(f"GitHub API failed for {url}")


def parse_link_header(link: str | None) -> dict[str, str]:
    links: dict[str, str] = {}
    if not link:
        return links
    for part in link.split(","):
        m = re.search(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if m:
            links[m.group(2)] = m.group(1)
    return links


def link_last_page(link: str | None, fallback_len: int) -> int:
    links = parse_link_header(link)
    last = links.get("last")
    if not last:
        return fallback_len
    m = re.search(r"[?&]page=(\d+)", last)
    return int(m.group(1)) if m else fallback_len


def iso_to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fmt_number(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    value = int(value)
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}M"
        return text.replace(".0M", "M")
    if value >= 100_000:
        return f"{value / 1000:.0f}k"
    if value >= 1_000:
        text = f"{value / 1000:.1f}k"
        return text.replace(".0k", "k")
    return str(value)


def fmt_date(value: str) -> str:
    if not value:
        return "n/a"
    return iso_to_datetime(value).strftime("%b %d, %Y")


def read_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"snapshots": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"snapshots": []}
    if not isinstance(data, dict) or not isinstance(data.get("snapshots"), list):
        return {"snapshots": []}
    return data


def write_history(path: Path, history: dict[str, Any], repos: list[dict[str, Any]], now: datetime) -> None:
    cutoff = now.date() - timedelta(days=HISTORY_DAYS)
    snapshots = []
    for snap in history.get("snapshots", []):
        try:
            snap_date = datetime.strptime(snap["date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            continue
        if snap_date >= cutoff:
            snapshots.append(snap)

    today = now.strftime("%Y-%m-%d")
    snapshots = [snap for snap in snapshots if snap.get("date") != today]
    snapshots.append(
        {
            "date": today,
            "repos": {
                repo["full_name"]: {
                    "stars": repo["stars"],
                    "forks": repo["forks"],
                    "pushed_at": repo["pushed_at"],
                }
                for repo in repos
            },
        }
    )
    snapshots.sort(key=lambda snap: snap["date"])
    path.write_text(json.dumps({"snapshots": snapshots}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def baseline_snapshot(history: dict[str, Any], now: datetime, days: int) -> dict[str, Any] | None:
    target = now.date() - timedelta(days=days)
    candidates = []
    for snap in history.get("snapshots", []):
        try:
            snap_date = datetime.strptime(snap["date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            continue
        if snap_date <= target:
            candidates.append((snap_date, snap))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return None


def fetch_repositories(client: GitHubClient, org: str, min_stars: int, pushed_cutoff: datetime) -> list[dict[str, Any]]:
    query = f"org:{org} stars:>={min_stars} pushed:>={pushed_cutoff.date().isoformat()} fork:false archived:false"
    repos = fetch_search_repositories(client, query, pushed_cutoff)
    repos = [repo for repo in repos if repo["stars"] >= min_stars]
    repos.sort(key=lambda repo: repo["stars"], reverse=True)
    return repos


def fetch_extra_section_repositories(client: GitHubClient, section: SourceSection, pushed_cutoff: datetime) -> list[dict[str, Any]]:
    query = f"{section.query} pushed:>={pushed_cutoff.date().isoformat()}"
    repos = fetch_search_repositories(client, query, pushed_cutoff)
    for repo in repos:
        repo["cluster_key"] = section.key
        repo["cluster_name"] = section.name
    repos.sort(key=lambda repo: (iso_to_datetime(repo["pushed_at"]), repo["stars"]), reverse=True)
    return repos


def fetch_search_repositories(client: GitHubClient, query: str, pushed_cutoff: datetime) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        data, _ = client.request(
            "/search/repositories",
            {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        items = data.get("items", [])
        if not items:
            break
        for item in items:
            if iso_to_datetime(item["pushed_at"]) < pushed_cutoff:
                continue
            repos.append(normalize_repo(item))
        if len(items) < 100 or page >= 10:
            break
        page += 1
    return repos


def normalize_repo(item: dict[str, Any]) -> dict[str, Any]:
    license_info = item.get("license") or {}
    license_spdx = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    if not license_spdx or license_spdx == "NOASSERTION":
        license_spdx = "n/a"
    return {
        "name": item.get("name", ""),
        "full_name": item.get("full_name", ""),
        "url": item.get("html_url", ""),
        "description": item.get("description") or "",
        "language": item.get("language") or "Mixed",
        "topics": item.get("topics") or [],
        "stars": int(item.get("stargazers_count") or 0),
        "forks": int(item.get("forks_count") or 0),
        "open_issues": int(item.get("open_issues_count") or 0),
        "pushed_at": item.get("pushed_at") or "",
        "updated_at": item.get("updated_at") or "",
        "created_at": item.get("created_at") or "",
        "license": license_spdx,
        "commits": None,
        "commits_30d": None,
    }


def enrich_commit_counts(client: GitHubClient, repos: list[dict[str, Any]], since: datetime) -> None:
    for index, repo in enumerate(repos, start=1):
        full_name = repo["full_name"]
        print(f"[{index:03d}/{len(repos):03d}] commits {full_name}")
        repo["commits"] = commit_count(client, full_name)
        repo["commits_30d"] = commit_count(client, full_name, since=since)


def commit_count(client: GitHubClient, full_name: str, since: datetime | None = None) -> int:
    params: dict[str, Any] = {"per_page": 1}
    if since:
        params["since"] = since.isoformat().replace("+00:00", "Z")
    try:
        data, headers = client.request(f"/repos/{full_name}/commits", params)
    except RuntimeError as exc:
        print(f"  warning: {exc}", file=sys.stderr)
        return 0
    if not isinstance(data, list) or not data:
        return 0
    return link_last_page(headers.get("link"), len(data))


def cluster_repo(repo: dict[str, Any]) -> Cluster:
    haystack = " ".join(
        [
            repo["name"],
            repo["description"],
            repo["language"],
            " ".join(repo.get("topics") or []),
        ]
    ).lower()
    name_overrides = {
        "weknora": "ai-agents-models",
        "ncnn": "ai-agents-models",
        "tnn": "ai-agents-models",
        "browserskill": "ai-agents-models",
        "teamai-cli": "ai-agents-models",
        "angelslim": "ai-agents-models",
        "wemm-embedding": "ai-agents-models",
        "hpc-ops": "ai-agents-models",
        "wesmartflow": "ai-agents-models",
        "yolo-master": "ai-agents-models",
        "ksanallm": "ai-agents-models",
        "angelspec": "ai-agents-models",
        "cognitivekernel-pro": "ai-agents-models",
        "openclaw-weixin": "ai-agents-models",
        "wave-mcp": "ai-agents-models",
        "workbuddy-bench": "ai-agents-models",
        "mmkv": "mobile-wechat-runtime",
        "tinker": "mobile-wechat-runtime",
        "hippy": "mobile-wechat-runtime",
        "puerts": "mobile-wechat-runtime",
        "shadow": "mobile-wechat-runtime",
        "wcdb": "mobile-wechat-runtime",
        "westore": "mobile-wechat-runtime",
        "tdesign": "design-web-miniprograms",
        "tdesign-vue-next": "design-web-miniprograms",
        "tdesign-vue": "design-web-miniprograms",
        "tdesign-react": "design-web-miniprograms",
        "tdesign-flutter": "design-web-miniprograms",
        "tdesign-miniprogram": "design-web-miniprograms",
        "tdesign-mobile-vue": "design-web-miniprograms",
        "tdesign-vue-next-starter": "design-web-miniprograms",
        "tdesign-vue-starter": "design-web-miniprograms",
        "tdesign-react-starter": "design-web-miniprograms",
        "tdesign-miniprogram-starter-retail": "design-web-miniprograms",
        "tmagic-editor": "design-web-miniprograms",
        "cherry-markdown": "design-web-miniprograms",
        "wujie": "design-web-miniprograms",
        "hel": "design-web-miniprograms",
        "omi": "design-web-miniprograms",
        "weui": "design-web-miniprograms",
        "spring-cloud-tencent": "cloud-data-distributed",
        "tendis": "cloud-data-distributed",
        "flare": "cloud-data-distributed",
        "tseer": "cloud-data-distributed",
        "tsw": "cloud-data-distributed",
        "tbase": "cloud-data-distributed",
        "caelus": "cloud-data-distributed",
        "tencentkona-8": "languages-jdks-devtools",
        "tencentkona-11": "languages-jdks-devtools",
        "biny": "languages-jdks-devtools",
        "bqlog": "languages-jdks-devtools",
        "loli_profiler": "languages-jdks-devtools",
        "libpag": "graphics-games-media",
        "tgfx": "graphics-games-media",
        "digitalhuman": "graphics-games-media",
        "tad_sim": "graphics-games-media",
        "unlua": "graphics-games-media",
        "sluaunreal": "graphics-games-media",
        "vap": "graphics-games-media",
        "ai-infra-guard": "security-reliability",
        "vulngym": "security-reliability",
        "aicgseceval": "security-reliability",
        "tencentkonasmsuite": "security-reliability",
    }
    clusters_by_key = {cluster.key: cluster for cluster in CLUSTERS}
    name_override = name_overrides.get(repo["name"].lower())
    if name_override:
        return clusters_by_key[name_override]

    override_terms = {
        "ai-agents-models": (
            "inference",
            "language model",
            "llm",
            "machine learning",
            "rag",
        ),
        "security-reliability": (
            "jailbreak",
            "red team",
            "vulnerability",
        ),
        "design-web-miniprograms": (
            "miniprogram",
            "tdesign",
            "ui components",
        ),
        "cloud-data-distributed": (
            "distributed",
            "redis",
            "spring cloud",
        ),
        "graphics-games-media": (
            "animation",
            "unreal",
            "unity",
        ),
        "mobile-wechat-runtime": (
            "android",
            "hotfix",
            "wechat",
        ),
        "languages-jdks-devtools": (
            "jdk",
            "openjdk",
            "profiler",
        ),
    }
    for key, terms in override_terms.items():
        if any(term_matches(haystack, term) for term in terms):
            return clusters_by_key[key]

    scores: list[tuple[int, int, Cluster]] = []
    for order, cluster in enumerate(CLUSTERS):
        score = 0
        for keyword in cluster.keywords:
            if keyword in haystack:
                score += 2 if keyword in repo["name"].lower() else 1
        scores.append((score, -order, cluster))
    best = max(scores, key=lambda item: (item[0], item[1]))
    if best[0] == 0:
        return CLUSTERS[0]
    return best[2]


def term_matches(haystack: str, term: str) -> bool:
    if " " in term:
        return term in haystack
    pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def add_derived_fields(repos: list[dict[str, Any]], history: dict[str, Any], now: datetime, traction_days: int) -> None:
    baseline = baseline_snapshot(history, now, traction_days)
    baseline_repos = baseline.get("repos", {}) if baseline else {}
    for repo in repos:
        cluster = cluster_repo(repo)
        repo["cluster_key"] = cluster.key
        repo["cluster_name"] = cluster.name
        old = baseline_repos.get(repo["full_name"])
        if old:
            repo["stars_delta_30d"] = max(0, repo["stars"] - int(old.get("stars", repo["stars"])))
            repo["forks_delta_30d"] = max(0, repo["forks"] - int(old.get("forks", repo["forks"])))
            repo["has_history_baseline"] = True
        else:
            repo["stars_delta_30d"] = None
            repo["forks_delta_30d"] = None
            repo["has_history_baseline"] = False
        repo["traction_score"] = traction_score(repo)


def traction_score(repo: dict[str, Any]) -> float:
    commits = int(repo.get("commits_30d") or 0)
    audience = (math.log10(max(repo["stars"], 10)) * 18) + (math.log10(max(repo["forks"], 1) + 1) * 6)
    if repo.get("has_history_baseline"):
        star_delta = int(repo.get("stars_delta_30d") or 0)
        fork_delta = int(repo.get("forks_delta_30d") or 0)
        return (star_delta * 10) + (fork_delta * 4) + min(commits, 350) + audience

    last_push = iso_to_datetime(repo["pushed_at"])
    age_days = max(0, (datetime.now(timezone.utc) - last_push).days)
    freshness = max(0.0, 1.0 - (age_days / TRACTION_DAYS))
    return min(commits, 350) + (freshness * audience)


def grouped_repos(repos: list[dict[str, Any]], top_n: int) -> dict[str, list[dict[str, Any]]]:
    groups = {cluster.key: [] for cluster in CLUSTERS}
    for repo in repos:
        groups[repo["cluster_key"]].append(repo)
    for key in groups:
        groups[key].sort(key=lambda repo: repo["stars"], reverse=True)
        if top_n > 0:
            groups[key] = groups[key][:top_n]
    return groups


def language_class(language: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", language.lower()).strip("-")
    known = {
        "c": "c",
        "c#": "csharp",
        "c++": "cpp",
        "cuda": "cuda",
        "go": "go",
        "javascript": "js",
        "python": "py",
        "shell": "shell",
        "typescript": "ts",
    }
    return known.get(language.lower(), normalized or "generic")


def detected_keywords(repo: dict[str, Any]) -> list[str]:
    haystack = f"{repo['name']} {repo['description']}".lower()
    found = []
    for label, aliases in AUTO_KEYWORD_TERMS:
        if label in SUBSTRING_KEYWORDS:
            matched = any(alias in haystack for alias in aliases)
        else:
            matched = any(term_matches(haystack, alias) for alias in aliases)
        if matched:
            found.append(label)
    return found


def keyword_buttons(repo: dict[str, Any]) -> str:
    keywords: list[str] = []
    seen: set[str] = set()
    for keyword in [*detected_keywords(repo), *(repo.get("topics") or [])]:
        key = keyword.lower()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(keyword)

    return "".join(
        (
            f'<button class="badge keyword-button" type="button" '
            f'data-filter-keyword="{escape(keyword)}" title="Filter by {escape(keyword)}">'
            f'{escape(keyword)}</button>'
        )
        for keyword in keywords
    )


def repo_row(repo: dict[str, Any], rank: int | None = None, include_cluster: bool = False, include_traction: bool = False) -> str:
    pushed_ts = int(iso_to_datetime(repo["pushed_at"]).timestamp()) if repo.get("pushed_at") else 0
    desc = escape(repo["description"] or "No description provided.")
    topic_html = keyword_buttons(repo)
    owner = repo["full_name"].split("/", 1)[0]
    avatar = f"https://github.com/{escape(owner)}.png?size=32"
    name_cell = (
        f'<div class="repo-name"><img src="{avatar}" alt="" class="avatar">'
        f'<a href="{escape(repo["url"])}" target="_blank" rel="noreferrer">{escape(repo["name"])}</a></div>'
        f'<a class="repo-slug" href="{escape(repo["url"])}" target="_blank" rel="noreferrer">{escape(repo["full_name"])}</a>'
    )
    language = escape(repo["language"])
    cells = []
    if rank is not None:
        cells.append(f'<td class="rank-cell">{rank}</td>')
        cells.append(f"<td>{name_cell}</td>")
        cells.append(description_cell(desc, topic_html))
        if include_cluster:
            cells.append(cluster_cell(repo, linked=include_traction))
        cells.append(f'<td><span class="tag tag-{language_class(repo["language"])}">{language}</span></td>')
    else:
        cells.append(f"<td>{name_cell}</td>")
        cells.append(f'<td><span class="tag tag-{language_class(repo["language"])}">{language}</span></td>')
        cells.append(description_cell(desc, topic_html))
        if include_cluster:
            cells.append(cluster_cell(repo, linked=include_traction))
    cells.append(github_cell(repo, include_activity=include_traction))
    return (
        f'<tr data-stars="{repo["stars"]}" data-pushed="{pushed_ts}" '
        f'data-name="{escape(repo["name"].lower())}" data-cluster="{escape(repo["cluster_key"])}">\n'
        + "\n".join(f"  {cell}" for cell in cells)
        + "\n</tr>"
    )


def description_cell(desc: str, topic_html: str) -> str:
    return f'<td class="description-cell">{desc}<div class="topic-row">{topic_html}</div></td>'


def cluster_cell(repo: dict[str, Any], linked: bool = False) -> str:
    name = escape(repo["cluster_name"])
    if linked:
        return f'<td><a class="cluster-pill cluster-link" href="#cluster-{escape(repo["cluster_key"])}">{name}</a></td>'
    return f'<td><span class="cluster-pill">{name}</span></td>'


def github_cell(repo: dict[str, Any], include_activity: bool = False) -> str:
    activity = ""
    if include_activity:
        pct = max(4, min(100, int(repo.get("traction_pct", 0))))
        activity = f"""
    <div class="activity-block" aria-label="Recent activity score {int(repo["traction_score"])}">
      <div class="activity-bar"><span style="width: {pct}%"></span></div>
      <div class="activity-meta">
        <span>{fmt_number(repo.get("commits_30d"))} commits / 30d</span>
        <span>score {int(repo["traction_score"])}</span>
      </div>
    </div>"""
    return f"""<td class="github-cell">
    <div class="gh-stats">
      <span class="star-count">★ {fmt_number(repo["stars"])}</span>
      <span class="fork-count">⑂ {fmt_number(repo["forks"])}</span>
      <span class="commit-count">⟳ {fmt_number(repo.get("commits"))}</span>
    </div>
    <span class="last-updated">⏱ {fmt_date(repo["pushed_at"])}</span>{activity}
  </td>"""


def section_table(cluster: Cluster, repos: list[dict[str, Any]], top_n: int) -> str:
    rows = "\n".join(repo_row(repo) for repo in repos)
    scope = f"Showing up to {top_n} repositories by stars." if top_n > 0 else "Showing all qualifying repositories in this cluster by stars."
    return f"""
<section class="repo-section" id="cluster-{cluster.key}" data-section>
  <div class="section-header">
    <div>
      <h2><span class="section-mark section-mark-{cluster.accent}"></span>{escape(cluster.name)}</h2>
      <p>{escape(cluster.summary)} {escape(scope)}</p>
    </div>
    <span class="count-pill">{len(repos)} repos</span>
  </div>
  <div class="table-wrap">
    <table>
      <colgroup>
        <col class="col-repository">
        <col class="col-language">
        <col class="col-description">
        <col class="col-github">
      </colgroup>
      <thead>
        <tr>
          <th>Repository</th>
          <th>Language</th>
          <th>Description</th>
          <th>GitHub</th>
        </tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
</section>
"""


def source_section_table(section: SourceSection, repos: list[dict[str, Any]]) -> str:
    rows = "\n".join(repo_row(repo) for repo in repos)
    return f"""
<section class="repo-section" id="cluster-{section.key}" data-section>
  <div class="section-header">
    <div>
      <h2><span class="section-mark section-mark-{section.accent}"></span>{escape(section.name)}</h2>
      <p>{escape(section.summary)} Showing {len(repos)} repositories sorted by recent activity.</p>
    </div>
    <span class="count-pill">{len(repos)} repos</span>
  </div>
  <div class="table-wrap">
    <table>
      <colgroup>
        <col class="col-repository">
        <col class="col-language">
        <col class="col-description">
        <col class="col-github">
      </colgroup>
      <thead>
        <tr>
          <th>Repository</th>
          <th>Language</th>
          <th>Description</th>
          <th>GitHub</th>
        </tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
</section>
"""


def traction_table(repos: list[dict[str, Any]], days: int) -> str:
    top = sorted(repos, key=lambda repo: repo["traction_score"], reverse=True)[:25]
    max_score = max((repo["traction_score"] for repo in top), default=1) or 1
    for repo in top:
        repo["traction_pct"] = round((repo["traction_score"] / max_score) * 100)
    rows = "\n".join(repo_row(repo, include_cluster=True, include_traction=True) for repo in top)
    return f"""
<section class="repo-section traction-section" id="section-traction" data-section>
  <div class="section-header">
    <div>
      <h2><span class="section-mark section-mark-blue"></span>Established Repos With Fresh Traction</h2>
      <p>Ranked by recent commits, freshness, stars, and forks so active projects with broad audiences rise first.</p>
    </div>
    <span class="count-pill">Top 25</span>
  </div>
  <div class="table-wrap">
    <table>
      <colgroup>
        <col class="col-traction-repository">
        <col class="col-traction-language">
        <col class="col-traction-description">
        <col class="col-traction-cluster">
        <col class="col-traction-github">
      </colgroup>
      <thead>
        <tr>
          <th>Repository</th>
          <th>Language</th>
          <th>Description</th>
          <th>Cluster</th>
          <th>GitHub</th>
        </tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
</section>
"""


def jump_links(extra_sections: dict[str, list[dict[str, Any]]]) -> str:
    cluster_items = [
        ("section-traction", "Fresh Traction", None),
        *[(f"cluster-{cluster.key}", cluster.name, None) for cluster in CLUSTERS],
    ]
    extra_items = [
        (f"cluster-{section.key}", section.name, len(extra_sections.get(section.key, [])))
        for section in EXTRA_SECTIONS
    ]

    def render_group(label: str, items: list[tuple[str, str, int | None]], class_name: str) -> str:
        links = "\n".join(
            f'<a href="#{escape(anchor)}">{escape(item_label)}{f" <span>{count}</span>" if count is not None else ""}</a>'
            for anchor, item_label, count in items
        )
        return f'<div class="jump-group {class_name}"><span class="jump-group-label">{escape(label)}</span>{links}</div>'

    groups = [render_group("Clusters", cluster_items, "jump-group-clusters")]
    if extra_items:
        groups.append(render_group("Other Repos", extra_items, "jump-group-other"))
    return "\n".join(groups)


def render_html(
    repos: list[dict[str, Any]],
    extra_sections: dict[str, list[dict[str, Any]]],
    now: datetime,
    pushed_cutoff: datetime,
    args: argparse.Namespace,
) -> str:
    groups = grouped_repos(repos, args.top_per_cluster)
    total_stars = sum(repo["stars"] for repo in repos)
    total_commits_30d = sum(int(repo.get("commits_30d") or 0) for repo in repos)
    sections = "\n".join(section_table(cluster, groups[cluster.key], args.top_per_cluster) for cluster in CLUSTERS)
    extra_section_html = "\n".join(
        source_section_table(section, extra_sections.get(section.key, []))
        for section in EXTRA_SECTIONS
    )
    traction = traction_table(repos, args.traction_days)
    nav_links = jump_links(extra_sections)
    updated = now.strftime("%Y-%m-%d %H:%M UTC")
    cutoff = pushed_cutoff.strftime("%Y-%m-%d")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script>
  var p = location.pathname;
  if (p.charAt(p.length - 1) !== '/') p += '/';
  document.write('<base href="' + p + '">');
</script>
<title>Tencent GitHub Repository Atlas</title>
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #07101a;
    --surface: #0e1724;
    --surface2: #121f30;
    --surface3: #18283b;
    --border: #26384d;
    --text: #eef6ff;
    --text-muted: #9db0c5;
    --tencent: #0052d9;
    --blue: #0052d9;
    --cyan: #12b7f5;
    --green: #22c55e;
    --orange: #f38020;
    --red: #ef4444;
    --pink: #f472b6;
    --purple: #7c6af7;
    --yellow: #fbbf24;
  }}

  * {{ box-sizing: border-box; }}

  html {{ scroll-behavior: smooth; }}

  body {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(circle at 85% -10%, rgba(0, 82, 217, 0.28), transparent 28rem),
      radial-gradient(circle at 8% 4%, rgba(18, 183, 245, 0.12), transparent 26rem),
      linear-gradient(180deg, #081827 0%, var(--bg) 42rem);
    color: var(--text);
    font-family: 'DM Sans', system-ui, sans-serif;
    padding: 40px 24px 52px;
  }}

  a {{ color: inherit; }}

  .header,
  .search-panel,
  .repo-section,
  .footer {{
    max-width: 1480px;
    margin-left: auto;
    margin-right: auto;
  }}

  .page-header {{
    margin-bottom: 24px;
  }}

  .page-title {{
    display: flex;
    align-items: center;
    gap: 14px;
    margin: 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 30px;
    line-height: 1.15;
    letter-spacing: -0.6px;
  }}

  .logo-box {{
    width: 38px;
    height: 38px;
    display: inline-grid;
    place-items: center;
    border-radius: 8px;
    background: linear-gradient(135deg, #0052d9, #12b7f5);
    color: #ffffff;
    font-weight: 800;
    box-shadow: 0 0 24px rgba(0, 82, 217, 0.32);
  }}

  .page-header p {{
    max-width: 920px;
    margin: 10px 0 0;
    color: var(--text-muted);
    line-height: 1.6;
    font-size: 14px;
  }}

  .metric-strip {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 18px;
  }}

  .metric-card {{
    min-width: 148px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: rgba(17, 22, 26, 0.8);
  }}

  .metric-card strong {{
    display: block;
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    color: var(--text);
  }}

  .metric-card span {{
    display: block;
    margin-top: 3px;
    color: var(--text-muted);
    font-size: 11px;
    font-family: 'JetBrains Mono', monospace;
  }}

  .jump-nav {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px 14px;
    margin-top: 18px;
    align-items: flex-start;
  }}

  .jump-group {{
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    align-items: center;
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: rgba(17, 22, 26, 0.48);
  }}

  .jump-group-label {{
    padding: 0 4px 0 2px;
    color: var(--text-muted);
    font: 700 10px 'JetBrains Mono', monospace;
    letter-spacing: 0.7px;
    text-transform: uppercase;
  }}

  .jump-nav a {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 1px solid var(--border);
    border-radius: 7px;
    padding: 8px 10px;
    background: rgba(17, 22, 26, 0.72);
    color: var(--text);
    text-decoration: none;
    font: 700 11px 'JetBrains Mono', monospace;
    transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
  }}

  .jump-group-clusters a {{
    border-color: rgba(0, 82, 217, 0.34);
    background: rgba(0, 82, 217, 0.07);
  }}

  .jump-group-other {{
    border-color: rgba(80, 230, 255, 0.22);
    background: rgba(80, 230, 255, 0.045);
  }}

  .jump-group-other a {{
    border-color: rgba(80, 230, 255, 0.36);
    background: rgba(80, 230, 255, 0.08);
  }}

  .jump-nav a:hover {{
    border-color: var(--tencent);
    color: var(--tencent);
    background: rgba(0, 82, 217, 0.1);
  }}

  .jump-group-other a:hover {{
    border-color: var(--cyan);
    color: var(--cyan);
    background: rgba(80, 230, 255, 0.12);
  }}

  .jump-nav span {{
    color: var(--text-muted);
    font-size: 10px;
  }}

  .search-panel {{
    display: grid;
    grid-template-columns: auto minmax(240px, 1fr) auto auto auto auto;
    gap: 10px;
    align-items: center;
    margin-bottom: 30px;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background:
      radial-gradient(circle at top right, rgba(0, 82, 217, 0.14), transparent 24rem),
      linear-gradient(180deg, rgba(23, 30, 35, 0.98), rgba(17, 22, 26, 0.98));
  }}

  .search-label,
  .sort-label {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-muted);
    white-space: nowrap;
  }}

  .search-input {{
    width: 100%;
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: rgba(8, 11, 13, 0.95);
    color: var(--text);
    outline: none;
    padding: 12px 14px;
    font: 500 14px 'DM Sans', system-ui, sans-serif;
  }}

  .search-input:focus {{
    border-color: rgba(0, 82, 217, 0.82);
    box-shadow: 0 0 0 3px rgba(0, 82, 217, 0.14);
  }}

  .button {{
    border: 1px solid var(--border);
    border-radius: 8px;
    background: rgba(8, 11, 13, 0.95);
    color: var(--text);
    padding: 12px 13px;
    font: 700 12px 'JetBrains Mono', monospace;
    cursor: pointer;
    white-space: nowrap;
  }}

  .button:hover,
  .button.active {{
    border-color: var(--tencent);
    color: var(--tencent);
  }}

  .button:disabled {{
    opacity: 0.55;
    cursor: default;
  }}

  .repo-section {{
    margin-top: 34px;
  }}

  .section-header {{
    display: flex;
    justify-content: space-between;
    gap: 18px;
    align-items: end;
    margin-bottom: 14px;
  }}

  .section-header h2 {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 20px;
    line-height: 1.25;
    letter-spacing: -0.3px;
  }}

  .section-header p {{
    margin: 6px 0 0;
    color: var(--text-muted);
    font-size: 13px;
    line-height: 1.5;
  }}

  .section-mark {{
    width: 10px;
    height: 20px;
    border-radius: 2px;
    background: var(--tencent);
    display: inline-block;
  }}

  .section-mark-blue {{ background: var(--blue); }}
  .section-mark-cyan {{ background: var(--cyan); }}
  .section-mark-orange {{ background: var(--orange); }}
  .section-mark-pink {{ background: var(--pink); }}
  .section-mark-purple {{ background: var(--purple); }}
  .section-mark-green {{ background: var(--green); }}
  .section-mark-red {{ background: var(--red); }}

  .count-pill,
  .cluster-pill {{
    display: inline-block;
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 4px 8px;
    color: var(--text-muted);
    background: rgba(8, 11, 13, 0.45);
    font: 600 11px 'JetBrains Mono', monospace;
  }}

  .count-pill {{
    white-space: nowrap;
  }}

  .cluster-pill {{
    white-space: normal;
    line-height: 1.35;
  }}

  .cluster-link {{
    text-decoration: none;
    transition: border-color 0.15s ease, color 0.15s ease;
  }}

  .cluster-link:hover {{
    color: var(--tencent);
    border-color: var(--tencent);
  }}

  .table-wrap {{
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--surface);
    scrollbar-color: #33516d #08111d;
    scrollbar-width: thin;
  }}

  table {{
    width: 100%;
    min-width: 1120px;
    border-collapse: collapse;
    font-size: 13px;
    table-layout: fixed;
  }}

  .traction-section table {{
    min-width: 1220px;
  }}

  .col-repository {{ width: 22%; }}
  .col-language {{ width: 10%; }}
  .col-description {{ width: 48%; }}
  .col-github {{ width: 20%; }}
  .col-traction-repository {{ width: 17%; }}
  .col-traction-description {{ width: 48%; }}
  .col-traction-cluster {{ width: 12%; }}
  .col-traction-language {{ width: 8%; }}
  .col-traction-github {{ width: 15%; }}

  th {{
    position: sticky;
    top: 0;
    z-index: 1;
    padding: 13px 14px;
    text-align: left;
    background: var(--surface2);
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    font: 700 11px 'JetBrains Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.7px;
  }}

  td {{
    padding: 13px 14px;
    vertical-align: top;
    border-bottom: 1px solid var(--border);
    line-height: 1.5;
  }}

  tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover {{ background: rgba(0, 82, 217, 0.045); }}

  .repo-name {{
    display: flex;
    align-items: center;
    gap: 8px;
    font: 700 14px 'JetBrains Mono', monospace;
    white-space: nowrap;
  }}

  .repo-name a {{
    color: var(--text);
    text-decoration: none;
  }}

  .repo-name a:hover,
  .repo-slug:hover {{
    color: var(--tencent);
    text-decoration: underline;
  }}

  .repo-slug {{
    display: block;
    margin-top: 4px;
    color: var(--text-muted);
    text-decoration: none;
    font: 500 11px 'JetBrains Mono', monospace;
  }}

  .avatar {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    background: var(--surface3);
  }}

  .tag,
  .badge {{
    display: inline-block;
    border-radius: 4px;
    padding: 2px 7px;
    font: 700 11px 'JetBrains Mono', monospace;
    background: rgba(149, 163, 154, 0.14);
    color: var(--text-muted);
  }}

  .tag-py {{ background: rgba(127, 186, 0, 0.14); color: var(--green); }}
  .tag-csharp {{ background: rgba(134, 97, 197, 0.18); color: var(--purple); }}
  .tag-c, .tag-cpp, .tag-cuda {{ background: rgba(69, 168, 255, 0.13); color: var(--blue); }}
  .tag-ts, .tag-js {{ background: rgba(250, 204, 21, 0.13); color: var(--yellow); }}
  .tag-go {{ background: rgba(80, 230, 255, 0.13); color: var(--cyan); }}
  .tag-shell {{ background: rgba(244, 114, 182, 0.13); color: var(--pink); }}

  .topic-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 7px;
  }}

  .badge {{
    font-size: 10px;
    color: #c0d1e4;
  }}

  .keyword-button {{
    border: 0;
    cursor: pointer;
    text-align: left;
    appearance: none;
    -webkit-appearance: none;
    transition: color 0.15s ease, background 0.15s ease;
  }}

  .keyword-button:hover,
  .keyword-button:focus-visible {{
    color: var(--tencent);
    background: rgba(0, 82, 217, 0.16);
    outline: none;
  }}

  .description-cell {{
    color: var(--text);
    max-width: 54rem;
  }}

  .github-cell {{
    min-width: 210px;
  }}

  .gh-stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 7px 10px;
    align-items: center;
  }}

  .star-count,
  .fork-count,
  .commit-count,
  .metric-sm {{
    font-family: 'JetBrains Mono', monospace;
  }}

  .star-count {{
    color: var(--yellow);
    font-weight: 800;
    font-size: 13px;
  }}

  .fork-count,
  .commit-count,
  .metric-sm {{
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
  }}

  .last-updated {{
    display: block;
    margin-top: 6px;
    color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
  }}

  .activity-block {{
    margin-top: 8px;
  }}

  .activity-bar {{
    height: 6px;
    border-radius: 999px;
    background: rgba(149, 163, 154, 0.16);
    overflow: hidden;
  }}

  .activity-bar span {{
    display: block;
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, #0052d9, #12b7f5);
  }}

  .activity-meta {{
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 5px;
    color: var(--text-muted);
    font: 600 10px 'JetBrains Mono', monospace;
  }}

  .number-cell,
  .rank-cell,
  .date-cell {{
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
  }}

  .number-cell {{
    color: var(--text);
    font-weight: 700;
  }}

  .score-cell {{
    color: var(--tencent);
  }}

  .date-cell {{
    color: var(--text-muted);
    font-size: 12px;
  }}

  .hidden {{
    display: none !important;
  }}

  .footer {{
    margin-top: 30px;
    color: var(--text-muted);
    font: 500 11px 'JetBrains Mono', monospace;
    line-height: 1.7;
  }}

  .footer a {{ color: var(--tencent); text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}

  @media (max-width: 900px) {{
    body {{ padding: 26px 14px 40px; }}
    .page-title {{ font-size: 24px; }}
    .search-panel {{ grid-template-columns: 1fr; }}
    .button, .search-label, .sort-label {{ width: 100%; }}
    .section-header {{ display: block; }}
    .count-pill {{ margin-top: 10px; }}
  }}
</style>
</head>
<body>
  <header class="header page-header">
    <h1 class="page-title"><span class="logo-box">TX</span>Tencent GitHub Repository Atlas</h1>
    <p>Searchable snapshot of public repositories from Tencent's <a href="https://github.com/orgs/Tencent/repositories" target="_blank" rel="noreferrer">Tencent organization</a>. Included repositories have more than 200 stars and were pushed within the last three months, since {cutoff}.</p>
    <div class="metric-strip" aria-label="Catalog summary">
      <div class="metric-card"><strong>{len(repos)}</strong><span>qualified repos</span></div>
      <div class="metric-card"><strong>{fmt_number(total_stars)}</strong><span>combined stars</span></div>
      <div class="metric-card"><strong>{fmt_number(total_commits_30d)}</strong><span>commits in {args.traction_days}d</span></div>
      <div class="metric-card"><strong>{len(CLUSTERS)}</strong><span>technology clusters</span></div>
    </div>
    <nav class="jump-nav" aria-label="Section links">
      {nav_links}
    </nav>
  </header>

  <div class="search-panel" role="search">
    <div class="search-label">Search</div>
    <input id="global-table-search" class="search-input" type="search" autocomplete="off" spellcheck="false" placeholder="repo, topic, language, description">
    <button id="global-search-clear" class="button" type="button" disabled>Clear</button>
    <div class="sort-label">Sort by</div>
    <button id="sort-stars" class="button" type="button">Stars</button>
    <button id="sort-fresh" class="button active" type="button">Freshness</button>
  </div>

{traction}
{sections}
{extra_section_html}

  <footer class="footer">
    <p style="margin-bottom: 10px;">Useful links:
      <a class="gh-link" href="https://opensource.tencent.com/" target="_blank" rel="noreferrer" style="display: inline; margin-left: 6px;">Tencent Open Source</a>
      <span style="margin: 0 4px;">·</span>
      <a class="gh-link" href="https://tdesign.tencent.com/" target="_blank" rel="noreferrer" style="display: inline;">TDesign</a>
      <span style="margin: 0 4px;">·</span>
      <a class="gh-link" href="https://cloud.tencent.com/" target="_blank" rel="noreferrer" style="display: inline;">Tencent Cloud</a>
    </p>
    <p style="text-align: center;">Generated from the GitHub API · Last updated: <span id="last-updated-date">{updated}</span></p>
  </footer>

<script>
  const searchInput = document.getElementById('global-table-search');
  const clearButton = document.getElementById('global-search-clear');
  const sortStars = document.getElementById('sort-stars');
  const sortFresh = document.getElementById('sort-fresh');

  function allRows() {{
    return Array.from(document.querySelectorAll('tbody tr'));
  }}

  function applySearch() {{
    const query = searchInput.value.trim().toLowerCase();
    let visibleRows = 0;
    document.querySelectorAll('[data-section]').forEach(section => {{
      let sectionVisible = false;
      section.querySelectorAll('tbody tr').forEach(row => {{
        const match = !query || row.textContent.toLowerCase().includes(query);
        row.classList.toggle('hidden', !match);
        if (match) {{
          sectionVisible = true;
          visibleRows += 1;
        }}
      }});
      section.classList.toggle('hidden', !sectionVisible);
    }});
    clearButton.disabled = !query;
    if (query && visibleRows === 0) {{
      document.querySelectorAll('[data-section], tbody tr').forEach(el => el.classList.remove('hidden'));
    }}
  }}

  function sortTables(kind) {{
    const attr = kind === 'fresh' ? 'pushed' : 'stars';
    document.querySelectorAll('tbody').forEach(tbody => {{
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => Number(b.dataset[attr] || 0) - Number(a.dataset[attr] || 0));
      rows.forEach(row => tbody.appendChild(row));
    }});
    sortStars.classList.toggle('active', kind === 'stars');
    sortFresh.classList.toggle('active', kind === 'fresh');
    applySearch();
  }}

  searchInput.addEventListener('input', applySearch);
  clearButton.addEventListener('click', () => {{
    searchInput.value = '';
    applySearch();
    searchInput.focus();
  }});
  document.addEventListener('click', event => {{
    const keyword = event.target.closest('[data-filter-keyword]');
    if (!keyword) return;
    const value = keyword.dataset.filterKeyword || keyword.textContent.trim();
    searchInput.value = searchInput.value.trim().toLowerCase() === value.trim().toLowerCase() ? '' : value;
    applySearch();
    searchInput.focus({{ preventScroll: true }});
  }});
  sortStars.addEventListener('click', () => sortTables('stars'));
  sortFresh.addEventListener('click', () => sortTables('fresh'));
  sortTables('fresh');
</script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)
    pushed_cutoff = subtract_months(now, args.months)
    traction_cutoff = now - timedelta(days=args.traction_days)

    token = get_token()
    if not token:
        print("warning: no GitHub token found; unauthenticated API rate limits may apply", file=sys.stderr)
    client = GitHubClient(token)
    repos = fetch_repositories(client, args.org, args.min_stars, pushed_cutoff)
    extra_sections = {
        section.key: fetch_extra_section_repositories(client, section, pushed_cutoff)
        for section in EXTRA_SECTIONS
    }
    all_repos = repos + [repo for section_repos in extra_sections.values() for repo in section_repos]
    if not args.skip_commit_counts:
        enrich_commit_counts(client, all_repos, traction_cutoff)

    history_path = Path(args.history)
    history = read_history(history_path)
    add_derived_fields(repos, history, now, args.traction_days)
    html = render_html(repos, extra_sections, now, pushed_cutoff, args)
    Path(args.file).write_text(html, encoding="utf-8")
    write_history(history_path, history, all_repos, now)
    print(f"Wrote {args.file} with {len(repos)} Tencent repositories")
    print(f"Wrote {args.history}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
