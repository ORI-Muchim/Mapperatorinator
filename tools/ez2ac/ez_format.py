"""Reader for EZ2AC ``.ez`` chart files.

Decryption: the whole file is reversed, then split into blocks of 8, 16, 8,
16, ... bytes (the last block takes whatever is left). Inside each block the
bytes are reversed and a 512-byte key table (``g_ezKeyTable``) is subtracted.
Because every block starts at the running offset, the key index of a byte is
simply its position in the reversed file modulo 512.

The key table is NOT part of this repository. Pass its path explicitly or set
the ``EZ2AC_EZ_KEY`` environment variable. Accepted formats: a raw 512-byte
binary file, or a C/C++ source file containing ``g_ezKeyTable[512] = {...}``.

Decrypted layout (little endian):
    header  0x96 bytes: signature(4) version(2) title(64) artist(64)
                        resolution(2, ticks per 4/4 measure) tempo(f32)
                        track_count(2) length(4, ticks) play_time(f32, s)
    track   0x4E bytes: signature(4) mode(2) name(64) length(4) event_bytes(4)
    event   13 bytes:   tick(4) type(1) payload(8)
        type 1 note   : sample(u16) volume(u8) pan(u8) attr(u8) duration(u16)
        type 2        : track volume
        type 3 tempo  : f32 BPM
        type 4 beat   : u8 beats per measure
        type 6 scroll : f32
        type 7 stop   : f32
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

KEY_SIZE = 512
HEADER_SIZE = 0x96
TRACK_HEADER_SIZE = 0x4E
EVENT_SIZE = 13

EVENT_NOTE = 1
EVENT_TRACK_VOLUME = 2
EVENT_TEMPO = 3
EVENT_BEAT = 4
EVENT_SCROLL = 6
EVENT_STOP = 7

# A note duration <= TAP_DURATION is a tap; larger values are hold lengths in ticks.
TAP_DURATION = 6


def load_key(path: str | os.PathLike | None = None, table: str = "g_ezKeyTable") -> bytes:
    """Load a 512-byte key table from a binary file or a C/C++ source file.

    ``table`` names the array to read from a source file: ``g_ezKeyTable`` for .ez charts,
    ``g_eziKeyTable`` for .ezi sample lists. The source declares the tables as ``int`` with
    values above 255; the game subtracts ``(char)value``, so only the low byte is kept.
    """
    path = path or os.environ.get("EZ2AC_EZ_KEY")
    if not path:
        raise ValueError("No .ez key given. Pass --key or set the EZ2AC_EZ_KEY environment variable.")
    data = Path(path).read_bytes()
    if len(data) == KEY_SIZE:
        return data
    text = data.decode("utf-8", errors="ignore")
    match = re.search(rf"\b{re.escape(table)}\s*\[[^\]]*\]\s*=\s*\{{([^}}]*)\}}", text)
    if match:
        values = [int(v, 0) for v in re.findall(r"0[xX][0-9a-fA-F]+|\d+", match.group(1))]
        if len(values) == KEY_SIZE:
            return bytes(v & 0xFF for v in values)
    raise ValueError(f"{path} is neither a {KEY_SIZE}-byte key file nor a source file with {table}[{KEY_SIZE}]")


def _blocks(n: int):
    offset, long_block = 0, 0
    while offset < n:
        if offset + 8 > n:
            length = n - offset
        else:
            length = 16 if long_block else 8
            if offset + length > n:
                length = n - offset
            long_block ^= 1
        yield offset, length
        offset += length


def decrypt(data: bytes, key: bytes) -> bytes:
    rev = data[::-1]
    out = bytearray(len(rev))
    for offset, length in _blocks(len(rev)):
        for j in range(length):
            p = offset + length - 1 - j
            out[offset + j] = (rev[p] - key[p % KEY_SIZE]) & 0xFF
    return bytes(out)


def encrypt(plain: bytes, key: bytes) -> bytes:
    out = bytearray(len(plain))
    for offset, length in _blocks(len(plain)):
        for j in range(length):
            p = offset + length - 1 - j
            out[p] = (plain[offset + j] + key[p % KEY_SIZE]) & 0xFF
    return bytes(out[::-1])


@dataclass
class Note:
    tick: int
    sample: int
    volume: int
    pan: int
    attr: int
    duration: int

    @property
    def is_hold(self) -> bool:
        return self.duration > TAP_DURATION


@dataclass
class Event:
    tick: int
    type: int
    payload: bytes

    def as_float(self) -> float:
        return struct.unpack_from("<f", self.payload)[0]

    def as_note(self) -> Note:
        sample, volume, pan, attr, duration = struct.unpack_from("<HBBBH", self.payload)
        return Note(self.tick, sample, volume, pan, attr, duration)


@dataclass
class Track:
    signature: bytes
    mode: int
    name: str
    length: int
    events: list[Event] = field(default_factory=list)

    def notes(self) -> list[Note]:
        return [e.as_note() for e in self.events if e.type == EVENT_NOTE]


@dataclass
class EzChart:
    signature: bytes
    version: int
    title: str
    artist: str
    resolution: int
    tempo: float
    length: int
    play_time: float
    tracks: list[Track]


def _cstr(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    for encoding in ("cp949", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def parse(plain: bytes) -> EzChart:
    if plain[:4] != b"EZFF":
        raise ValueError(f"Not a decrypted .ez chart (signature {plain[:4]!r})")
    version = struct.unpack_from("<H", plain, 4)[0]
    title, artist = _cstr(plain[6:70]), _cstr(plain[70:134])
    resolution, tempo, track_count, length, play_time = struct.unpack_from("<HfHIf", plain, 134)
    pos = HEADER_SIZE
    tracks = []
    for _ in range(track_count):
        signature = plain[pos:pos + 4]
        mode = struct.unpack_from("<H", plain, pos + 4)[0]
        name = _cstr(plain[pos + 6:pos + 70])
        track_length, event_bytes = struct.unpack_from("<II", plain, pos + 70)
        pos += TRACK_HEADER_SIZE
        if event_bytes % EVENT_SIZE or pos + event_bytes > len(plain):
            raise ValueError(f"Corrupt track at offset {pos}: {event_bytes} event bytes")
        events = []
        for e in range(pos, pos + event_bytes, EVENT_SIZE):
            tick, event_type = struct.unpack_from("<IB", plain, e)
            events.append(Event(tick, event_type, plain[e + 5:e + EVENT_SIZE]))
        pos += event_bytes
        tracks.append(Track(signature, mode, name, track_length, events))
    if pos != len(plain):
        raise ValueError(f"Trailing data: parsed {pos} of {len(plain)} bytes")
    return EzChart(plain[:4], version, title, artist, resolution, tempo, length, play_time, tracks)


def read_ez(path: str | os.PathLike, key: bytes) -> EzChart:
    return parse(decrypt(Path(path).read_bytes(), key))


@dataclass
class SampleEntry:
    """One line of a decrypted .ezi: the slot a note's ``sample`` indexes, the load kind and the file."""
    slot: int
    kind: int
    name: str  # as written in the .ezi, e.g. "0-MR.wav"; the game opens the .ssf with the same stem


def parse_ezi(plain: bytes) -> list[SampleEntry]:
    """Parse a decrypted .ezi (the game's .ezz): lines of "<slot> <kind> <path> [<second path>]"."""
    entries = []
    for line in plain.decode("cp949", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            entries.append(SampleEntry(int(parts[0]), int(parts[1]), parts[2]))
    return entries


def read_ezi(path: str | os.PathLike, key: bytes) -> list[SampleEntry]:
    """Read an .ezi sample list; ``key`` is the g_eziKeyTable (``load_key(path, "g_eziKeyTable")``)."""
    return parse_ezi(decrypt(Path(path).read_bytes(), key))
