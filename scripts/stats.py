#!/usr/bin/env python3
"""Generate profile stats cards that include private repositories.

Reads a token from STATS_TOKEN, queries the GitHub GraphQL API as that user and
writes two SVG cards into stats/. Only totals are written: repository names
never appear in the output.

    python scripts/stats.py              # real data, needs STATS_TOKEN
    python scripts/stats.py --placeholder  # cards that say stats are pending
    python scripts/stats.py --sample       # made-up data, for previewing the design
"""

import json
import os
import sys
import urllib.request
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "stats"
API = "https://api.github.com/graphql"
TOP_LANGUAGES = 8
# Languages that are mostly generated or config rather than written by hand.
IGNORED_LANGUAGES = {"Dockerfile", "Makefile", "Batchfile", "Shell", "PowerShell"}

CSS = """
<style>
  .bg { fill: #ffffff; stroke: #d0d7de; }
  .title { fill: #c2185b; font: 600 18px 'Segoe UI', Ubuntu, sans-serif; }
  .label { fill: #57606a; font: 400 13px 'Segoe UI', Ubuntu, sans-serif; }
  .value { fill: #1f2328; font: 600 13px 'Segoe UI', Ubuntu, sans-serif; }
  .note { fill: #8c959f; font: 400 11px 'Segoe UI', Ubuntu, sans-serif; }
  .track { fill: #eaeef2; }
  @media (prefers-color-scheme: dark) {
    .bg { fill: #0d1117; stroke: #30363d; }
    .title { fill: #fe428e; }
    .label { fill: #8b949e; }
    .value { fill: #e6edf3; }
    .note { fill: #6e7681; }
    .track { fill: #21262d; }
  }
</style>
"""


def graphql(token, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise SystemExit(f"GraphQL error: {payload['errors']}")
    return payload["data"]


def collect(token):
    years = graphql(
        token, "{ viewer { contributionsCollection { contributionYears } } }"
    )["viewer"]["contributionsCollection"]["contributionYears"]

    year_query = """
    query($from: DateTime!, $to: DateTime!) {
      viewer { contributionsCollection(from: $from, to: $to) {
        totalCommitContributions
        restrictedContributionsCount
        contributionCalendar { totalContributions }
      } }
    }"""
    all_time_commits = 0
    for year in years:
        c = graphql(
            token,
            year_query,
            {"from": f"{year}-01-01T00:00:00Z", "to": f"{year}-12-31T23:59:59Z"},
        )["viewer"]["contributionsCollection"]
        all_time_commits += c["totalCommitContributions"]

    last_year = graphql(
        token,
        """{ viewer { contributionsCollection {
              totalCommitContributions
              totalPullRequestContributions
              contributionCalendar { totalContributions }
            } } }""",
    )["viewer"]["contributionsCollection"]

    repo_query = """
    query($after: String) {
      viewer { repositories(first: 50, after: $after, ownerAffiliations: OWNER, isFork: false) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isPrivate
          languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
            edges { size node { name color } }
          }
        }
      } }
    }"""
    languages, colors = {}, {}
    public = private = 0
    after = None
    while True:
        page = graphql(token, repo_query, {"after": after})["viewer"]["repositories"]
        for repo in page["nodes"]:
            if repo["isPrivate"]:
                private += 1
            else:
                public += 1
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                if name in IGNORED_LANGUAGES:
                    continue
                languages[name] = languages.get(name, 0) + edge["size"]
                colors[name] = edge["node"]["color"] or "#8b949e"
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    return {
        "contributions_year": last_year["contributionCalendar"]["totalContributions"],
        "commits_year": last_year["totalCommitContributions"],
        "prs_year": last_year["totalPullRequestContributions"],
        "commits_all": all_time_commits,
        "repos_public": public,
        "repos_private": private,
        "languages": languages,
        "colors": colors,
    }


def card(width, height, title, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
        f"{CSS}"
        f'<rect class="bg" x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6"/>'
        f'<text class="title" x="24" y="38">{escape(title)}</text>'
        f"{body}</svg>\n"
    )


def overview_svg(s):
    rows = [
        ("Contributions, last 12 months", f"{s['contributions_year']:,}"),
        ("Commits, last 12 months", f"{s['commits_year']:,}"),
        ("Commits, all time", f"{s['commits_all']:,}"),
        ("Pull requests, last 12 months", f"{s['prs_year']:,}"),
        ("Repositories", f"{s['repos_public']} public · {s['repos_private']} private"),
    ]
    body = ""
    for i, (label, value) in enumerate(rows):
        y = 74 + i * 26
        body += f'<text class="label" x="24" y="{y}">{escape(label)}</text>'
        body += f'<text class="value" x="396" y="{y}" text-anchor="end">{escape(value)}</text>'
    body += '<text class="note" x="24" y="218">Includes private repositories</text>'
    return card(420, 234, "GitHub activity", body)


def languages_svg(s):
    total = sum(s["languages"].values()) or 1
    top = sorted(s["languages"].items(), key=lambda kv: kv[1], reverse=True)[:TOP_LANGUAGES]
    width = 420
    bar_w = width - 48
    body = f'<rect class="track" x="24" y="56" width="{bar_w}" height="10" rx="5"/>'
    body += '<clipPath id="bar"><rect x="24" y="56" width="%d" height="10" rx="5"/></clipPath><g clip-path="url(#bar)">' % bar_w
    x = 24.0
    for name, size in top:
        w = bar_w * size / total
        body += f'<rect x="{x:.2f}" y="56" width="{w:.2f}" height="10" fill="{s["colors"][name]}"/>'
        x += w
    body += "</g>"
    for i, (name, size) in enumerate(top):
        col, row = i % 2, i // 2
        cx = 24 + col * 190
        cy = 96 + row * 24
        body += f'<circle cx="{cx + 5}" cy="{cy - 4}" r="5" fill="{s["colors"][name]}"/>'
        body += f'<text class="label" x="{cx + 16}" y="{cy}">{escape(name)}</text>'
        body += f'<text class="value" x="{cx + 176}" y="{cy}" text-anchor="end">{size / total:.1%}</text>'
    rows = (len(top) + 1) // 2
    height = 96 + rows * 24 + 14
    body += f'<text class="note" x="24" y="{height - 16}">By code size, public and private repositories</text>'
    return card(width, height + 6, "Languages", body)


def placeholder_svg(title):
    body = '<text class="label" x="24" y="74">Stats appear after the first scheduled update.</text>'
    return card(420, 100, title, body)


SAMPLE = {
    "contributions_year": 1234,
    "commits_year": 987,
    "prs_year": 42,
    "commits_all": 2345,
    "repos_public": 6,
    "repos_private": 15,
    "languages": {"TypeScript": 50, "GDScript": 30, "JavaScript": 12, "Python": 8, "HTML": 4, "C#": 2},
    "colors": {"TypeScript": "#3178c6", "GDScript": "#355570", "JavaScript": "#f1e05a",
               "Python": "#3572A5", "HTML": "#e34c26", "C#": "#178600"},
}


def main():
    OUT.mkdir(exist_ok=True)
    if "--placeholder" in sys.argv:
        overview, langs = placeholder_svg("GitHub activity"), placeholder_svg("Languages")
    else:
        if "--sample" in sys.argv:
            stats = SAMPLE
        else:
            token = os.environ.get("STATS_TOKEN")
            if not token:
                raise SystemExit("STATS_TOKEN is not set")
            stats = collect(token)
        overview, langs = overview_svg(stats), languages_svg(stats)
    (OUT / "overview.svg").write_text(overview)
    (OUT / "languages.svg").write_text(langs)
    print(f"wrote {OUT / 'overview.svg'} and {OUT / 'languages.svg'}")


if __name__ == "__main__":
    main()
