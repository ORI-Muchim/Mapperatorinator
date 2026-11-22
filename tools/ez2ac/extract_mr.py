"""Find the MR (backing track) of every EZ2AC 5K chart, extract it to OGG and write the manifest for build_dataset.py.

Usage:
    python tools/ez2ac/extract_mr.py --key KSongEZ.cpp --sound "<game>/Sound" --out datasets/ez2ac_src

How the MR is chosen, per chart, from its .ezi sample list and the .ssf headers:
    * candidates are the samples at least --min-seconds long that a non-lane track plays
    * the MR is the longest candidate, and its first trigger is the MR start (mr_sample in the manifest)
    * the chart is left out when the MR covers less than --min-coverage of the time from that trigger to the
      last lane note (the music is split into loops), or when the second-longest candidate is at least
      --max-second of the MR's length (the music is split into stems such as bass and strings)

Output:
    <out>/mr/<song>/<sample>.ogg   the MR audio; a song gets one file per distinct MR sample its charts use
    <out>/mr_manifest.csv          song,audio,chart,mr_sample,mr_ms for every chart with an MR
    <out>/mr_report.csv            every chart: the chosen sample, its length and coverage, or why it was left out

The .ssf files are 18-byte PCM headers (channels, rate, byte rate, block align, bits, data size) followed by the
samples. Existing OGG files are kept unless --force is given, so an interrupted run can be resumed.
"""
from __future__ import annotations

import argparse
import csv
import os
import struct
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ez_format as ez  # type: ignore
    import ez2osu  # type: ignore
else:
    from . import ez_format as ez
    from . import ez2osu

SSF_HEADER = struct.Struct("<HIIHHI")  # channels, rate, byte rate, block align, bits, data size
PCM_FORMATS = {8: "u8", 16: "s16le", 24: "s24le", 32: "s32le"}


@dataclass(frozen=True)
class Ssf:
    path: Path
    channels: int
    rate: int
    byte_rate: int
    bits: int
    data_size: int

    @property
    def seconds(self) -> float:
        return self.data_size / self.byte_rate if self.byte_rate else 0.0


def read_ssf_header(path: Path) -> Ssf:
    with path.open("rb") as f:
        channels, rate, byte_rate, _align, bits, data_size = SSF_HEADER.unpack(f.read(SSF_HEADER.size))
    return Ssf(path, channels, rate, byte_rate, bits, data_size)


class SongFolder:
    """The .ssf files of one song folder, looked up by stem without regard to case (as Windows opens them)."""

    def __init__(self, folder: Path):
        self.files = {p.stem.lower(): p for p in folder.glob("*.ssf")}
        self.headers: dict[Path, Ssf] = {}

    def ssf(self, ezi_name: str) -> Ssf | None:
        path = self.files.get(PureWindowsPath(ezi_name).stem.lower())
        if path is None:
            return None
        if path not in self.headers:
            self.headers[path] = read_ssf_header(path)
        return self.headers[path]


def choose_mr(chart: ez.EzChart, entries: list[ez.SampleEntry], folder: SongFolder, args) -> dict:
    """Pick the MR of one chart; returns the report fields (``mr`` is the Ssf, or None with ``reason``)."""
    triggers: dict[int, list[int]] = defaultdict(list)
    last_lane_tick = 0
    for i, track in enumerate(chart.tracks):
        for note in track.notes():
            if i in ez2osu.LANE_TRACKS:
                last_lane_tick = max(last_lane_tick, note.tick + (note.duration if note.is_hold else 0))
            else:
                triggers[note.sample].append(note.tick)

    missing = 0
    candidates = []  # (seconds, slot, ssf)
    for entry in entries:
        ssf = folder.ssf(entry.name)
        if ssf is None:
            missing += 1
        elif ssf.seconds >= args.min_seconds and entry.slot in triggers:
            candidates.append((ssf.seconds, entry.slot, ssf))
    candidates.sort(key=lambda c: (-c[0], c[1]))

    out = {"entries": len(entries), "missing_ssf": missing, "candidates": len(candidates), "mr": None}
    if not candidates:
        out["reason"] = f"no played sample of {args.min_seconds:g}s or more"
        return out
    seconds, slot, ssf = candidates[0]
    tempo = ez2osu.TempoMap(chart)
    start_ms = tempo.ms_at(min(triggers[slot]))
    span_ms = tempo.ms_at(last_lane_tick) - start_ms
    coverage = seconds * 1000 / span_ms if span_ms > 0 else 0.0
    second = candidates[1][0] / seconds if len(candidates) > 1 else 0.0
    out.update(mr_sample=slot, sample_file=ssf.path.name, seconds=round(seconds, 2), mr_ms=round(start_ms, 3),
               coverage=round(coverage, 3), second_ratio=round(second, 3), triggers=len(triggers[slot]))
    if coverage < args.min_coverage:
        out["reason"] = f"longest sample covers {coverage:.0%} of the chart (music split into loops?)"
    elif second >= args.max_second:
        out["reason"] = (f"second sample {candidates[1][2].path.name} is {second:.0%} of the longest "
                         "(music split into stems?)")
    else:
        out["mr"] = ssf
    return out


def ssf_to_ogg(ssf: Ssf, target: Path, ffmpeg: str, quality: float) -> None:
    pcm_format = PCM_FORMATS.get(ssf.bits)
    if pcm_format is None:
        raise ValueError(f"{ssf.path}: unsupported {ssf.bits}-bit PCM")
    with ssf.path.open("rb") as f:
        f.seek(SSF_HEADER.size)
        pcm = f.read(ssf.data_size)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", pcm_format, "-ar", str(ssf.rate), "-ac", str(ssf.channels),
         "-i", "pipe:0", "-c:a", "libvorbis", "-q:a", f"{quality:g}", "-f", "ogg", str(partial)],
        input=pcm, check=True)
    os.replace(partial, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", help="KSongEZ.cpp with g_ezKeyTable and g_eziKeyTable (default: $EZ2AC_EZ_KEY)")
    parser.add_argument("--sound", type=Path, required=True, help="the game's Sound folder (one folder per song)")
    parser.add_argument("--out", type=Path, required=True, help="output folder for mr/, the manifest and the report")
    parser.add_argument("--chart-glob", default="5keymix1p*.ez", help="charts to use in each song folder")
    parser.add_argument("--min-seconds", type=float, default=20.0, help="shortest sample that can be an MR")
    parser.add_argument("--min-coverage", type=float, default=0.6,
                        help="the MR must last this fraction of the time from its start to the last lane note")
    parser.add_argument("--max-second", type=float, default=0.5,
                        help="leave a chart out when its second-longest candidate is this fraction of the MR")
    parser.add_argument("--quality", type=float, default=5, help="libvorbis quality (-q:a)")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--force", action="store_true", help="re-encode OGG files that already exist")
    parser.add_argument("--dry-run", action="store_true", help="write the report and manifest, encode nothing")
    args = parser.parse_args(argv)

    ez_key = ez.load_key(args.key)
    ezi_key = ez.load_key(args.key, "g_eziKeyTable")
    args.out.mkdir(parents=True, exist_ok=True)

    report_rows, manifest_rows = [], []
    jobs: dict[Path, Ssf] = {}
    for song_dir in sorted(p for p in args.sound.iterdir() if p.is_dir()):
        charts = sorted(song_dir.glob(args.chart_glob))
        if not charts:
            continue
        folder = SongFolder(song_dir)
        for chart_path in charts:
            row = {"song": song_dir.name, "chart": chart_path.name}
            ezi_path = chart_path.with_suffix(".ezi")
            if not ezi_path.is_file():
                report_rows.append({**row, "reason": "no .ezi"})
                continue
            result = choose_mr(ez.read_ez(chart_path, ez_key), ez.read_ezi(ezi_path, ezi_key), folder, args)
            mr = result.pop("mr")
            report_rows.append({**row, **result, "included": mr is not None})
            if mr is None:
                continue
            target = args.out / "mr" / song_dir.name / (mr.path.stem + ".ogg")
            jobs[target] = mr
            manifest_rows.append({**row, "audio": target.relative_to(args.out).as_posix(),
                                  "mr_sample": result["mr_sample"], "mr_ms": ""})

    fields = ["song", "chart", "included", "reason", "mr_sample", "sample_file", "seconds", "mr_ms", "coverage",
              "second_ratio", "triggers", "candidates", "entries", "missing_ssf"]
    with (args.out / "mr_report.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(report_rows)
    with (args.out / "mr_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["song", "audio", "chart", "mr_sample", "mr_ms"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    songs = {r["song"] for r in report_rows}
    kept = {r["song"] for r in manifest_rows}
    print(f"{len(manifest_rows)} of {len(report_rows)} charts have an MR ({len(kept)} of {len(songs)} songs); "
          f"{len(jobs)} MR files")
    left_out = sorted(songs - kept)
    if left_out:
        print(f"Songs without any usable MR ({len(left_out)}): {', '.join(left_out)}")
    retriggered = [r for r in report_rows if r.get("included") and r.get("triggers", 1) > 1]
    if retriggered:
        print(f"{len(retriggered)} charts trigger their MR more than once (see 'triggers' in mr_report.csv)")

    todo = {t: s for t, s in jobs.items() if args.force or not t.is_file()}
    if args.dry_run or not todo:
        print("Nothing to encode." if not args.dry_run else f"Dry run: {len(todo)} OGG files would be encoded.")
        return
    print(f"Encoding {len(todo)} OGG files with {args.workers} workers ...")
    failures = []
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(ssf_to_ogg, s, t, args.ffmpeg, args.quality): t for t, s in todo.items()}
        for done, (future, target) in enumerate(futures.items(), 1):
            try:
                future.result()
            except Exception as e:  # keep going; the failed files are listed at the end
                failures.append((target, e))
            if done % 50 == 0 or done == len(futures):
                print(f"  {done}/{len(futures)}")
    if failures:
        for target, e in failures:
            print(f"FAILED {target}: {e}")
        raise SystemExit(f"{len(failures)} OGG files failed; rerun to retry them.")
    print(f"Wrote {args.out / 'mr_manifest.csv'}")


if __name__ == "__main__":
    main()
