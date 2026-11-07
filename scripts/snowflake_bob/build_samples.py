# ruff: noqa: E501  -- data tables and page text, one row to a line
"""Build the sample reports under docs/reports/ from the made-up data set.

    python3 scripts/snowflake_bob/build_samples.py           # write the pages, the SQL and the CSV
    python3 scripts/snowflake_bob/build_samples.py --check   # compare with the committed files; writes nothing

No account, no network. The data has a fixed seed, so the same code always gives the same bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from snowflake_bob import backends, demo_data, page, reports  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs" / "reports"
DAY = demo_data.LAST_DAY.isoformat()
SITE, QUEUE, GROUP = "S1022", "4788", "Claims"
SAMPLES = {"morning-pulse": {"day": DAY},
           "network-panorama": {"queue": QUEUE, "end": DAY, "days": 45},
           "forecast-vs-actual": {"site": SITE, "group": GROUP, "start": "2026-09-08", "end": DAY}}
BLURBS = {"morning-pulse": "How team leads treated the day's suggested staffing plans, across the network.",
          "network-panorama": "One queue across all sites, day by day.",
          "forecast-vs-actual": "The engine's forecast against contacts handled, for one queue group at one site."}

INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bob Insights samples</title><style>
:root{--bg:#f4f6f8;--card:#fff;--ink:#1a1a1a;--ink2:#5c5c5c;--line:#e6e9ec}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px}a.c{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px 16px;margin:10px 0;color:inherit;text-decoration:none}a.c b{display:block;font-size:17px}a.c span{color:var(--ink2)}
p{color:var(--ink2)}</style></head><body><main><h1>Bob Insights samples</h1>
<p>Three interactive reports built from a made-up contact-centre group, Acme. The UK cities are real; every site, queue, person and number here comes from a random
generator with a fixed seed. Each page is one self-contained file: no external scripts, no tracking.</p>
__CARDS__
</main></body></html>
"""


def files() -> dict[str, str]:
    """Published file name -> content."""
    backend = backends.DemoBackend()
    out = {}
    cards = []
    for kind, params in SAMPLES.items():
        report = reports.build(kind, backend, **params)
        out[f"{kind}.html"] = report.html
        out[f"{kind}.sql"] = report.sql
        if kind == "network-panorama":
            out[f"{kind}.csv"] = report.csv
        cards.append(f'<a class="c" href="{kind}.html"><b>{page.PRODUCTS[kind]}</b><span>{BLURBS[kind]}</span></a>')
    out["index.html"] = INDEX.replace("__CARDS__", "\n".join(cards))
    return out


def main(argv: list[str]) -> int:
    wanted = files()
    if "--check" in argv:
        bad = [name for name, text in wanted.items() if not (OUT / name).is_file() or (OUT / name).read_text(encoding="utf-8") != text]
        extra = sorted(p.name for p in OUT.iterdir() if p.name not in wanted) if OUT.is_dir() else []
        for name in bad:
            print(f"differs: {name}")
        for name in extra:
            print(f"not generated: {name}")
        return 1 if bad or extra else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in wanted.items():
        (OUT / name).write_text(text, encoding="utf-8")
        print(OUT / name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
