"""One-off survey of public results pages.

Honest User-Agent, checks each site's robots.txt first and only downloads pages
that robots.txt allows. One request per page, with a pause between requests.
"""
import json, sys, time, urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests

UA = "LottLab/1.0 (personal results tracker; +https://github.com/mylucky69/lott-lab)"
PAGES = [
    "https://www.thelott.com/oz-lotto/results",
    "https://www.lotteryextreme.com/australia/ozlotto-results",
    "https://www.lotteryextreme.com/australia/powerball-results",
    "https://www.lotteryextreme.com/australia/saturdaylotto-results",
    "https://www.lotteryvalley.com/australia/past-results",
    "https://lotteryguru.com/australia-lottery-results/au-oz-lotto/au-oz-lotto-results-history",
    "https://australia.national-lottery.com/oz-lotto/results-archive-2025",
    "https://www.lottolyzer.com/history/australia/oz-lotto",
    "https://www.ozlotteries.com/oz-lotto/results",
    "https://www.lotterywest.wa.gov.au/games/oz-lotto",
]
out_dir = Path(sys.argv[1]); out_dir.mkdir(parents=True, exist_ok=True)
report, robots = [], {}
for url in PAGES:
    host = urlparse(url).netloc
    entry = {"url": url}
    if host not in robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": UA}, timeout=20)
            (out_dir / f"{host}.robots.txt").write_text(r.text[:20000])
            rp.parse(r.text.splitlines()) if r.status_code == 200 else rp.parse([])
            entry["robots_status"] = r.status_code
        except Exception as e:
            rp.parse([]); entry["robots_error"] = str(e)[:200]
        robots[host] = rp
        time.sleep(2)
    allowed = robots[host].can_fetch(UA, url)
    entry["robots_allows"] = allowed
    if allowed:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            entry.update(status=r.status_code, length=len(r.text), type=r.headers.get("content-type"))
            name = (host + urlparse(url).path).replace("/", "_")[:120] + ".html"
            (out_dir / name).write_text(r.text[:400000])
            entry["saved"] = name
        except Exception as e:
            entry["error"] = str(e)[:200]
        time.sleep(3)
    report.append(entry)
    print(entry)
(out_dir / "report.json").write_text(json.dumps(report, indent=1))
