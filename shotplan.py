#!/usr/bin/env python3
"""Compile a CSV shot list into an offline storyboard, prompts and budget summary."""
import argparse
import base64
import csv
from decimal import Decimal, InvalidOperation
import html
import io
import json
from pathlib import Path
import re
import sys

FIELDS = {"shot_id", "duration_seconds", "description", "camera", "prompt", "attempts", "unit_cost"}
FORMATS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def number(value, field, line, positive=False):
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("line %s: %s must be a number" % (line, field))
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("line %s: %s must be %s" % (line, field, "positive and finite" if positive else "nonnegative and finite"))
    return result


def read_shots(path):
    path = Path(path).resolve()
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("CSV exceeds the 2 MB limit")
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)):
        raise ValueError("CSV contains duplicate column names")
    if not FIELDS.issubset(headers):
        raise ValueError("missing columns: " + ", ".join(sorted(FIELDS - set(headers))))
    if set(headers) - FIELDS - {"reference"}:
        raise ValueError("unknown columns: " + ", ".join(sorted(set(headers) - FIELDS - {"reference"})))
    shots, seen = [], set()
    for row in reader:
        line = reader.line_num
        if None in row or any(value is None for value in row.values()):
            raise ValueError("line %s: wrong number of columns" % line)
        row = {key: value.strip() for key, value in row.items()}
        shot_id = row["shot_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", shot_id):
            raise ValueError("line %s: shot_id must use 1–40 letters, digits, underscores or hyphens" % line)
        if shot_id in seen:
            raise ValueError("line %s: duplicate shot_id %s" % (line, shot_id))
        seen.add(shot_id)
        for field in ("description", "camera", "prompt"):
            if not row[field]:
                raise ValueError("line %s: %s is empty" % (line, field))
        duration = number(row["duration_seconds"], "duration_seconds", line, positive=True)
        if not re.fullmatch(r"[1-9][0-9]{0,3}", row["attempts"]):
            raise ValueError("line %s: attempts must be an integer from 1 to 9999" % line)
        cost = number(row["unit_cost"], "unit_cost", line)
        reference = row.get("reference", "")
        image = ""
        if reference:
            ref_path = (path.parent / reference).resolve()
            try:
                ref_path.relative_to(path.parent)
            except ValueError:
                raise ValueError("line %s: reference must stay inside the CSV directory" % line)
            if ref_path.suffix.lower() not in FORMATS:
                raise ValueError("line %s: references must be PNG, JPEG or WebP" % line)
            if ref_path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("line %s: reference exceeds 10 MB" % line)
            image = "data:%s;base64,%s" % (FORMATS[ref_path.suffix.lower()], base64.b64encode(ref_path.read_bytes()).decode("ascii"))
        shots.append(dict(row, duration=duration, attempts=int(row["attempts"]), cost=cost, image=image))
        if len(shots) > 200:
            raise ValueError("shot list exceeds the 200-shot limit")
    if not shots:
        raise ValueError("CSV has no shots")
    return shots


def decimal_text(value):
    return format(value, "f")


def summarize(shots, currency):
    return {
        "currency": currency,
        "shots": len(shots),
        "duration_seconds": decimal_text(sum((s["duration"] for s in shots), Decimal(0))),
        "planned_attempts": sum(s["attempts"] for s in shots),
        "estimated_generation_cost": decimal_text(sum((s["cost"] * s["attempts"] for s in shots), Decimal(0))),
        "cost_basis": "User-entered unit cost per attempt. Excludes editing, subscriptions, tax and unplanned retries.",
        "shot_costs": [{"shot_id": s["shot_id"], "estimated_cost": decimal_text(s["cost"] * s["attempts"])} for s in shots],
    }


def storyboard(shots, title, summary):
    esc = html.escape
    cards = []
    elapsed = Decimal(0)
    for s in shots:
        frame = '<img alt="Reference for %s" src="%s">' % (esc(s["shot_id"]), s["image"]) if s["image"] else '<div class="placeholder"><span>%s</span><small>REFERENCE FRAME</small></div>' % esc(s["shot_id"])
        cards.append('''<article><div class="frame">%s</div><div class="details"><div class="meta"><b>%s</b><span>%ss → %ss</span></div><h2>%s</h2><p class="camera">%s</p><details><summary>Generation prompt</summary><p class="prompt">%s</p></details><div class="cost">%s attempts × %s %s = <b>%s %s</b></div></div></article>''' % (
            frame, esc(s["shot_id"]), decimal_text(elapsed), decimal_text(elapsed + s["duration"]), esc(s["description"]), esc(s["camera"]), esc(s["prompt"]), s["attempts"], esc(summary["currency"]), decimal_text(s["cost"]), esc(summary["currency"]), decimal_text(s["cost"] * s["attempts"])))
        elapsed += s["duration"]
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s — Shotplan</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f3ee;color:#20251f;font-family:ui-sans-serif,system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:45px 5vw}.brand{font-size:12px;letter-spacing:2px;color:#65724c}h1{font-size:clamp(32px,5vw,64px);font-weight:500;letter-spacing:-2px;max-width:950px}.stats{display:flex;gap:40px;flex-wrap:wrap;margin:30px 0 45px;padding:24px 0;border-top:1px solid #d4d9c9;border-bottom:1px solid #d4d9c9}.stats b{display:block;font-size:28px;font-weight:450}.stats span{font-size:11px;color:#738066}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}article{border:1px solid #d4d9c9;background:#fafbf6;break-inside:avoid}.frame{aspect-ratio:16/9;background:#dfe5d4}.frame img{display:block;width:100%%;height:100%%;object-fit:contain}.placeholder{height:100%%;display:flex;align-items:center;justify-content:center;flex-direction:column;color:#8b987b;gap:10px}.placeholder span{font-size:48px;font-weight:300}.placeholder small{font-size:8px;letter-spacing:2px}.details{padding:18px}.meta{display:flex;justify-content:space-between;font-size:10px;color:#69785c}.meta b{color:#31472c}h2{font-size:18px;font-weight:550;line-height:1.4}.camera{font-size:12px;color:#69785c;line-height:1.6}summary{font-size:11px;cursor:pointer;padding:12px 0}.prompt{font-size:12px;line-height:1.7;white-space:pre-wrap}.cost{font-size:10px;border-top:1px solid #d4d9c9;padding-top:14px;margin-top:12px;color:#69785c}footer{margin-top:35px;font-size:11px;color:#69785c;line-height:1.8}footer a{color:inherit}@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.grid{grid-template-columns:1fr}.stats{gap:20px}}@media print{body{background:white}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}details{display:none}main{padding:0}}
</style><main><div class="brand">SHOTPLAN / PRE-PRODUCTION</div><h1>%s</h1><div class="stats"><div><b>%s</b><span>SHOTS</span></div><div><b>%ss</b><span>PLANNED RUNTIME</span></div><div><b>%s %s</b><span>ESTIMATED GENERATION</span></div><div><b>%s</b><span>PLANNED ATTEMPTS</span></div></div><section class="grid">%s</section><footer>%s<br>Timing assumes straight cuts with no overlaps. Reference slots are placeholders until you add images.<br>Compiled locally with <a href="https://github.com/satoshiwhynot/shotplan">Shotplan</a> by satoshi 001.</footer></main></html>''' % (
        esc(title), esc(title), summary["shots"], summary["duration_seconds"], esc(summary["currency"]), summary["estimated_generation_cost"], summary["planned_attempts"], "\n".join(cards), esc(summary["cost_basis"]))


def prompts(shots, title):
    lines = ["# " + title, "", "Planning prompts. No generation has been submitted.", ""]
    for s in shots:
        lines.extend(["## " + s["shot_id"], "", s["description"], "", "Duration: %ss · Camera: %s" % (decimal_text(s["duration"]), s["camera"]), "", s["prompt"], ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="UTF-8 CSV shot list")
    parser.add_argument("--out", type=Path, default=Path("build"), help="output directory (default: build)")
    parser.add_argument("--title", default="Untitled film", help="storyboard title")
    parser.add_argument("--currency", default="USD", help="currency label; does not convert prices")
    args = parser.parse_args(argv)
    try:
        shots = read_shots(args.csv)
        summary = summarize(shots, args.currency)
        outputs = {
            "storyboard.html": storyboard(shots, args.title, summary),
            "prompts.md": prompts(shots, args.title),
            "summary.json": json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        }
        args.out.mkdir(parents=True, exist_ok=True)
        for name, text in outputs.items():
            target = args.out / name
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(target)
        print("%s shots · %ss · estimated %s %s" % (summary["shots"], summary["duration_seconds"], args.currency, summary["estimated_generation_cost"]))
        print("Open " + str(args.out / "storyboard.html"))
        return 0
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        print("shotplan: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
