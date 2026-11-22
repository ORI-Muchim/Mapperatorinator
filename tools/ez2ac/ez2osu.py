"""Convert EZ2AC 5K ``.ez`` charts to osu!mania 5K ``.osu`` files.

Usage:
    python tools/ez2ac/ez2osu.py --key KEY chart.ez -o chart.osu --audio mr.ogg [--mr-sample 0 | --mr-ms 3000]

Conversion rules:
    * tracks 3-7 are the five lanes; every other playable track (turntable,
      pedal, effects) is dropped, BGM keysound tracks (22+) are ignored
    * duration <= 6 -> tap (type 1), duration > 6 -> hold ending at tick + duration (type 128)
    * the tempo map comes from track 0 (type 3 tempo, type 4 beats per measure),
      with 48 ticks per beat (resolution 192 = one 4/4 measure)
    * every time is shifted by -mr_ms so that the MR (backing track) starts at
      0 ms in the audio file; mr_ms is the time of the first note that plays the
      MR sample
"""
from __future__ import annotations

import argparse
import bisect
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ez_format as ez  # type: ignore
else:
    from . import ez_format as ez

LANE_TRACKS = (3, 4, 5, 6, 7)
FIRST_BGM_TRACK = 22

DIFFICULTY_LABELS = {"": "NM", "nm": "NM", "hd": "HD", "shd": "SHD", "ex": "EX"}


class TempoMap:
    """Tick -> milliseconds conversion with tempo and meter changes."""

    def __init__(self, chart: ez.EzChart):
        self.ticks_per_beat = chart.resolution / 4
        tempos: dict[int, float] = {}
        meters: dict[int, int] = {}
        self.ignored: list[str] = []
        for event in chart.tracks[0].events:
            if event.type == ez.EVENT_TEMPO:
                tempos[event.tick] = event.as_float()  # the last value at a tick wins
            elif event.type == ez.EVENT_BEAT:
                meters[event.tick] = event.payload[0]
            elif event.type in (ez.EVENT_STOP, ez.EVENT_SCROLL):
                self.ignored.append(f"type {event.type} at tick {event.tick} not supported")
        # The header tempo is authoritative for tick 0 (some charts carry stray 120 BPM events there).
        tempos[0] = chart.tempo
        meters.setdefault(0, 4)

        self.tempo_ticks = sorted(t for t in tempos if tempos[t] > 0)
        self.tempo_values = [tempos[t] for t in self.tempo_ticks]
        self.meter_ticks = sorted(meters)
        self.meter_values = [meters[t] or 4 for t in self.meter_ticks]

        # Prefix sums of milliseconds at each tempo change.
        self._ms = [0.0]
        for i in range(1, len(self.tempo_ticks)):
            dt = self.tempo_ticks[i] - self.tempo_ticks[i - 1]
            self._ms.append(self._ms[-1] + dt * self._ms_per_tick(self.tempo_values[i - 1]))

    def _ms_per_tick(self, bpm: float) -> float:
        return 60000.0 / (bpm * self.ticks_per_beat)

    def ms_at(self, tick: float) -> float:
        i = max(bisect.bisect_right(self.tempo_ticks, tick) - 1, 0)
        return self._ms[i] + (tick - self.tempo_ticks[i]) * self._ms_per_tick(self.tempo_values[i])

    def bpm_at(self, tick: int) -> float:
        return self.tempo_values[max(bisect.bisect_right(self.tempo_ticks, tick) - 1, 0)]

    def meter_at(self, tick: int) -> int:
        return self.meter_values[max(bisect.bisect_right(self.meter_ticks, tick) - 1, 0)]


def find_mr_tick(chart: ez.EzChart, mr_sample: int) -> int | None:
    """Earliest tick at which a note plays the given sample (any non-lane track)."""
    ticks = [n.tick for i, track in enumerate(chart.tracks) if i not in LANE_TRACKS
             for n in track.notes() if n.sample == mr_sample]
    return min(ticks) if ticks else None


def guess_mr_sample(chart: ez.EzChart) -> int | None:
    """Guess the MR sample: the first .ezi entry is index 0 or 1, and the MR is triggered first.

    Some charts also use the other index as a keysound (e.g. sample 0 near the end of the
    song), so pick whichever of 0 and 1 is played earliest on a BGM track.
    """
    first: dict[int, tuple[int, int]] = {}
    for i, track in enumerate(chart.tracks):
        if i < FIRST_BGM_TRACK:
            continue
        for n in track.notes():
            if n.sample in (0, 1):
                first[n.sample] = min(first.get(n.sample, (n.tick, i)), (n.tick, i))
    if not first:
        return None
    return min(first, key=lambda s: (first[s], s))


def difficulty_label(stem: str) -> str:
    parts = stem.split("-")
    suffix = parts[-1].lower() if len(parts) >= 3 else ""
    return DIFFICULTY_LABELS.get(suffix, suffix.upper())


@dataclass
class ConversionReport:
    taps: int = 0
    holds: int = 0
    dropped_negative: int = 0
    dropped_overlap: int = 0
    mr_ms: float = 0.0
    playtime_error_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)


@dataclass
class OsuMetadata:
    title: str
    version: str
    audio_filename: str = "audio.ogg"
    artist: str = ""
    creator: str = "EZ2AC"
    source: str = "EZ2AC"
    tags: str = ""
    beatmap_id: int = 0
    beatmapset_id: int = -1
    hp: float = 8
    od: float = 8


def convert(chart: ez.EzChart, meta: OsuMetadata, mr_ms: float = 0.0, keys: int = 5) -> tuple[str, ConversionReport]:
    tempo = TempoMap(chart)
    report = ConversionReport(mr_ms=mr_ms, warnings=list(tempo.ignored))
    # The header play time is length / header tempo, so it only checks charts without BPM changes.
    if len(set(tempo.tempo_values)) == 1:
        report.playtime_error_ms = tempo.ms_at(chart.length) - chart.play_time * 1000

    # Uninherited timing points at every tempo or meter change.
    timing_lines = []
    last = None
    for tick in sorted(set(tempo.tempo_ticks) | set(tempo.meter_ticks)):
        bpm, meter = tempo.bpm_at(tick), tempo.meter_at(tick)
        if last == (bpm, meter):
            continue
        last = (bpm, meter)
        timing_lines.append(f"{tempo.ms_at(tick) - mr_ms:.3f},{60000.0 / bpm:.12g},{meter},1,0,100,1,0")

    hit_objects = []  # (time, line)
    for column, track_index in enumerate(LANE_TRACKS):
        x = int((column + 0.5) * 512 / keys)
        busy_until = -1e18
        for note in sorted(chart.tracks[track_index].notes(), key=lambda n: n.tick):
            start = round(tempo.ms_at(note.tick) - mr_ms)
            if start < 0:
                report.dropped_negative += 1
                continue
            if start <= busy_until:
                report.dropped_overlap += 1
                continue
            if note.is_hold:
                end = round(tempo.ms_at(note.tick + note.duration) - mr_ms)
                hit_objects.append((start, column, f"{x},192,{start},128,0,{end}:0:0:0:0:"))
                report.holds += 1
                busy_until = end
            else:
                hit_objects.append((start, column, f"{x},192,{start},1,0,0:0:0:0:"))
                report.taps += 1
                busy_until = start
    hit_objects.sort()
    if report.dropped_negative:
        report.warnings.append(f"{report.dropped_negative} notes before the MR start were dropped")

    text = "\n".join([
        "osu file format v14",
        "",
        "[General]",
        f"AudioFilename: {meta.audio_filename}",
        "AudioLeadIn: 0",
        "PreviewTime: -1",
        "Countdown: 0",
        "SampleSet: Normal",
        "StackLeniency: 0.7",
        "Mode: 3",
        "LetterboxInBreaks: 0",
        "SpecialStyle: 0",
        "WidescreenStoryboard: 0",
        "",
        "[Editor]",
        "DistanceSpacing: 1",
        "BeatDivisor: 4",
        "GridSize: 4",
        "TimelineZoom: 1",
        "",
        "[Metadata]",
        f"Title:{meta.title}",
        f"TitleUnicode:{meta.title}",
        f"Artist:{meta.artist}",
        f"ArtistUnicode:{meta.artist}",
        f"Creator:{meta.creator}",
        f"Version:{meta.version}",
        f"Source:{meta.source}",
        f"Tags:{meta.tags}",
        f"BeatmapID:{meta.beatmap_id}",
        f"BeatmapSetID:{meta.beatmapset_id}",
        "",
        "[Difficulty]",
        f"HPDrainRate:{meta.hp:g}",
        f"CircleSize:{keys}",
        f"OverallDifficulty:{meta.od:g}",
        "ApproachRate:5",
        "SliderMultiplier:1.4",
        "SliderTickRate:1",
        "",
        "[Events]",
        "//Background and Video events",
        "//Break Periods",
        "",
        "[TimingPoints]",
        *timing_lines,
        "",
        "",
        "[HitObjects]",
        *[line for _, _, line in hit_objects],
        "",
    ])
    return text, report


def resolve_mr_ms(chart: ez.EzChart, mr_sample: int | None = None, mr_ms: float | None = None) -> tuple[float, str]:
    """Return (mr_ms, source). An explicit mr_ms wins over a sample index; otherwise the sample is guessed."""
    if mr_ms is not None:
        return float(mr_ms), "explicit"
    source = "manifest"
    if mr_sample is None:
        mr_sample, source = guess_mr_sample(chart), "guessed"
    if mr_sample is None:
        return 0.0, "none"
    tick = find_mr_tick(chart, mr_sample)
    if tick is None:
        return 0.0, f"sample {mr_sample} not found"
    return TempoMap(chart).ms_at(tick), source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("chart", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--key", help="g_ezKeyTable: 512-byte binary or C++ source (default: $EZ2AC_EZ_KEY)")
    parser.add_argument("--audio", default="audio.ogg", help="AudioFilename written to the .osu")
    parser.add_argument("--title")
    parser.add_argument("--mr-sample", type=int, help="sample index of the MR (default: guessed)")
    parser.add_argument("--mr-ms", type=float, help="MR start time in ms (overrides --mr-sample)")
    args = parser.parse_args(argv)

    chart = ez.read_ez(args.chart, ez.load_key(args.key))
    mr_ms, source = resolve_mr_ms(chart, args.mr_sample, args.mr_ms)
    meta = OsuMetadata(
        title=args.title or args.chart.parent.name,
        version=difficulty_label(args.chart.stem),
        audio_filename=args.audio,
    )
    text, report = convert(chart, meta, mr_ms)
    output = args.output or args.chart.with_suffix(".osu")
    output.write_text(text, encoding="utf-8")
    print(f"{output}: {report.taps} taps, {report.holds} holds, mr_ms={mr_ms:.1f} ({source})")
    for warning in report.warnings:
        print(f"  warning: {warning}")


if __name__ == "__main__":
    main()
