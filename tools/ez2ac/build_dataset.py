"""Build a Mapperatorinator ``mmrs`` training dataset from EZ2AC 5K charts.

Usage:
    python tools/ez2ac/build_dataset.py --key KEY --ez-root EZ2AC_5K --manifest mr_manifest.csv --out datasets/ez2ac_5k

Input:
    --ez-root   one folder per song containing the .ez charts (e.g. 10Anti/5keymix1p-10anti-hd.ez)
    --manifest  CSV with the MR audio for each song. Columns:
                    song       song folder name (required)
                    audio      path to the MR audio file, absolute or relative to the manifest (required)
                    chart      chart file name; when set the row applies to that chart only (optional)
                    mr_sample  sample index of the MR in the chart (optional)
                    mr_ms      MR start time in milliseconds (optional, overrides mr_sample)
                Songs without an audio row are skipped. Without mr_sample/mr_ms the MR sample is guessed.

Output (the layout read by osuT5/osuT5/dataset/mmrs_dataset.py):
    <out>/data/<song>/audio.<ext>       copy of the MR audio
    <out>/data/<song>/<chart>.osu       osu!mania 5K chart, timed so the MR starts at 0 ms
    <out>/metadata.parquet              one row per chart
    <out>/conversion_report.csv         per-chart conversion details and warnings
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
import zlib
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ez_format as ez  # type: ignore
    import ez2osu  # type: ignore
else:
    from . import ez_format as ez
    from . import ez2osu

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)  # StarRating speeds expected by mmrs_dataset._get_difficulty
# IDs far above real osu! IDs so they never collide with the pretrained tokenizer's beatmap tables.
BEATMAP_ID_BASE = 900_000_000
BEATMAPSET_ID_BASE = 9_000_000


def stable_id(name: str, base: int, span: int, used: set[int]) -> int:
    value = base + zlib.crc32(name.encode("utf-8")) % span
    while value in used:
        value += 1
    used.add(value)
    return value


def star_ratings(osu_text: str) -> list[float]:
    import rosu_pp_py as rosu

    beatmap = rosu.Beatmap(content=osu_text)
    return [float(rosu.Difficulty(clock_rate=speed).calculate(beatmap).stars) for speed in SPEEDS]


def read_manifest(path: Path) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    songs, charts = {}, {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("song"):
                continue
            if row.get("audio"):
                audio = Path(row["audio"])
                row["audio"] = str(audio if audio.is_absolute() else (path.parent / audio))
            if row.get("chart"):
                charts[(row["song"], row["chart"])] = row
            else:
                songs[row["song"]] = row
    return songs, charts


def optional_number(value: str | None, cast):
    return cast(value) if value not in (None, "") else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", help="g_ezKeyTable: 512-byte binary or C++ source (default: $EZ2AC_EZ_KEY)")
    parser.add_argument("--ez-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--year", type=int, default=2024, help="year written to SubmittedDate/RankedDate (default: 2024)")
    parser.add_argument("--user-id", type=int, default=0, help="UserId for every chart (default: 0, unknown mapper)")
    parser.add_argument("--keys", type=int, default=5)
    parser.add_argument("--min-notes", type=int, default=50, help="skip charts with fewer notes after conversion")
    args = parser.parse_args(argv)

    import pandas as pd

    key = ez.load_key(args.key)
    songs, chart_rows = read_manifest(args.manifest)
    data_dir = args.out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    rows, report_rows = [], []
    used_set_ids, used_map_ids = set(), set()
    song_dirs = sorted(p for p in args.ez_root.iterdir() if p.is_dir())
    skipped_songs = []
    date = datetime(args.year, 1, 1)

    for song_dir in song_dirs:
        song = song_dir.name
        charts = sorted(song_dir.glob("*.ez"))
        if not charts:
            continue
        song_row = songs.get(song, {})
        audio_src = song_row.get("audio") or next((chart_rows[(song, c.name)].get("audio") for c in charts
                                                   if chart_rows.get((song, c.name), {}).get("audio")), None)
        if not audio_src or not Path(audio_src).is_file():
            skipped_songs.append(song)
            continue

        out_dir = data_dir / song
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_name = "audio" + Path(audio_src).suffix.lower()
        shutil.copyfile(audio_src, out_dir / audio_name)
        set_id = stable_id(song, BEATMAPSET_ID_BASE, 90_000_000, used_set_ids)

        for chart_path in charts:
            row = {**song_row, **chart_rows.get((song, chart_path.name), {})}
            chart = ez.read_ez(chart_path, key)
            mr_ms, mr_source = ez2osu.resolve_mr_ms(
                chart, optional_number(row.get("mr_sample"), int), optional_number(row.get("mr_ms"), float))
            beatmap_id = stable_id(f"{song}/{chart_path.name}", BEATMAP_ID_BASE, 99_999_999, used_map_ids)
            meta = ez2osu.OsuMetadata(
                title=song,
                version=ez2osu.difficulty_label(chart_path.stem),
                audio_filename=audio_name,
                beatmap_id=beatmap_id,
                beatmapset_id=set_id,
            )
            text, report = ez2osu.convert(chart, meta, mr_ms, keys=args.keys)
            report_row = {
                "song": song, "chart": chart_path.name, "beatmap_id": beatmap_id, "mr_ms": round(mr_ms, 3),
                "mr_source": mr_source, "taps": report.taps, "holds": report.holds,
                "dropped_before_mr": report.dropped_negative, "dropped_overlap": report.dropped_overlap,
                "warnings": " | ".join(report.warnings), "included": False,
            }
            report_rows.append(report_row)
            if report.taps + report.holds < args.min_notes:
                report_row["warnings"] = (report_row["warnings"] + " | too few notes").strip(" |")
                continue

            osu_name = chart_path.stem + ".osu"
            (out_dir / osu_name).write_text(text, encoding="utf-8")
            stars = star_ratings(text)
            report_row["included"] = True
            rows.append({
                "BeatmapSetId": set_id,
                "Id": beatmap_id,
                "ModeInt": 3,
                "BeatmapSetFolder": song,
                "BeatmapFile": osu_name,
                "AudioFile": audio_name,
                "StarRating": stars,
                "DifficultyRating": stars[SPEEDS.index(1.0)],
                "SubmittedDate": date,
                "RankedDate": date,
                "Ranked": 1,
                "UserId": args.user_id,
                "Title": song,
                "Version": meta.version,
                "OmdbTags": [],
            })

    if not rows:
        raise SystemExit("No charts were converted: check --manifest audio paths.")
    pd.DataFrame(rows).to_parquet(args.out / "metadata.parquet", index=False)
    with (args.out / "conversion_report.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(report_rows[0].keys()))
        writer.writeheader()
        writer.writerows(report_rows)

    n_sets = len({r["BeatmapSetId"] for r in rows})
    guessed = sum(r["mr_source"] == "guessed" for r in report_rows if r["included"])
    print(f"Wrote {len(rows)} charts from {n_sets} songs to {args.out}")
    print(f"Skipped {len(skipped_songs)} songs without audio" + (f": {', '.join(skipped_songs[:10])}..." if skipped_songs else ""))
    if guessed:
        print(f"{guessed} charts use a guessed MR sample; add mr_sample or mr_ms to the manifest to be exact.")
    print(f"Set train/test split by song index in the training config (0..{n_sets}).")


if __name__ == "__main__":
    main()
