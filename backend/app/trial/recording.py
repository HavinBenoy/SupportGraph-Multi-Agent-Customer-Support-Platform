"""Recording (M5): a provider interface, a local-disk provider, and the recorder a live call feeds. Recording is OFF unless all three hold:
  (1) SG_TRIAL_RECORDING_PROVIDER=local (the default `none` can never record), (2) an administrator offers recording in the trial settings,
  (3) the caller explicitly consented on THIS call. Withdrawal, a storage error, the call ending or the mode being switched off stop it at once.

Storage: files, never database blobs. One directory per recording under SG_TRIAL_RECORDINGS_DIR (mode 0700, files 0600) holding up to three WAV tracks:
`caller.wav` (16 kHz), `assistant.wav` (the voice's own rate) and `staff.wav` (16 kHz). Each track is padded with silence to follow the call's clock, so the three
can be laid on one timeline later. The database holds only an opaque `storage_ref`; no API returns it, and no path is ever built from user input.

This is a basic recorder: tracks are written as they arrive (a crash leaves a track whose WAV header says zero length, and the next startup marks such a
recording failed so retention removes it). It has been exercised with synthetic audio only."""

import logging
import os
import re
import shutil
import struct
import threading
import time
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)
TRACKS = ("caller", "assistant", "staff")
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class RecordingUnavailable(Exception):
    """The provider cannot store audio right now (directory missing/unwritable, disk full). Recording must not start (or must stop)."""

    reason = "storage_unavailable"


class RecordingStopped(Exception):
    """The recorder stopped itself (for example the size limit); it must be finalised, not continued."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Handle(Protocol):
    def write(self, track: str, pcm: bytes, rate: int) -> None: ...
    def close(self) -> int: ...      # finalise; returns the total bytes stored
    def abort(self) -> None: ...     # stop without finalising (a failure)


class RecordingProvider(Protocol):
    name: str

    def healthy(self) -> bool: ...
    def open(self, tenant_id: str, recording_id: str) -> tuple[str, Handle]: ...      # -> (storage_ref, handle)
    def delete(self, ref: str) -> None: ...                                           # idempotent: a missing recording is a success
    def exists(self, ref: str) -> bool: ...
    def track_path(self, ref: str, track: str) -> Path | None: ...                    # for an authorised download; None if absent


class NullProvider:
    """Records nothing, ever. The default: the consent and retention framework runs, no audio is stored."""

    name = "none"

    def healthy(self) -> bool:
        return False

    def open(self, tenant_id: str, recording_id: str):  # noqa: ARG002
        raise RecordingUnavailable("no recording provider is configured")

    def delete(self, ref: str) -> None:  # noqa: ARG002
        return None

    def exists(self, ref: str) -> bool:  # noqa: ARG002
        return False

    def track_path(self, ref: str, track: str):  # noqa: ARG002
        return None


class _Track:
    """One WAV file that follows the call clock: a gap since the last write becomes silence, so tracks line up."""

    def __init__(self, path: Path, rate: int, t0: float, limit: int):
        self.path, self.rate, self.t0, self.limit = path, rate, t0, limit
        self.f = open(path, "wb", opener=lambda p, fl: os.open(p, fl, 0o600))
        self.f.write(b"\0" * 44)                                   # header placeholder, patched on close
        self.samples = 0

    def write(self, pcm: bytes) -> int:
        n = len(pcm) // 2
        if n == 0:
            return 0
        expected = int((time.monotonic() - self.t0) * self.rate)
        gap = expected - self.samples - n                          # how far behind the call clock this write is
        added = 0
        if gap > self.rate // 50:                                  # more than 20 ms of silence: pad it (bounded: never more than a minute at once)
            pad = min(gap, self.rate * 60)
            self.f.write(b"\0" * (pad * 2))
            self.samples += pad
            added += pad * 2
        self.f.write(pcm[: n * 2])
        self.samples += n
        return added + n * 2

    def close(self) -> int:
        size = self.samples * 2
        self.f.flush()
        self.f.seek(0)
        self.f.write(b"RIFF" + struct.pack("<I", 36 + size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, self.rate, self.rate * 2, 2, 16)
                     + b"data" + struct.pack("<I", size))
        self.f.close()
        return size


class _LocalHandle:
    def __init__(self, directory: Path, max_bytes: int):
        self.dir, self.max_bytes, self.total = directory, max_bytes, 0
        self.t0 = time.monotonic()
        self.tracks: dict[str, _Track] = {}
        self.lock = threading.Lock()
        self.closed = False

    def write(self, track: str, pcm: bytes, rate: int) -> None:
        if track not in TRACKS or self.closed:
            return
        with self.lock:
            try:
                t = self.tracks.get(track)
                if t is None:
                    t = self.tracks[track] = _Track(self.dir / f"{track}.wav", rate, self.t0, self.max_bytes)
                self.total += t.write(pcm)
            except OSError as e:
                raise RecordingUnavailable(f"write failed ({type(e).__name__})") from None
        if self.total >= self.max_bytes:
            raise RecordingStopped("size_limit")

    def close(self) -> int:
        with self.lock:
            if self.closed:
                return self.total
            self.closed = True
            total = 0
            for t in self.tracks.values():
                try:
                    total += t.close()
                except OSError:
                    pass
            self.total = total
            return total

    def abort(self) -> None:
        with self.lock:
            self.closed = True
            for t in self.tracks.values():
                try:
                    t.f.close()
                except OSError:
                    pass


class LocalProvider:
    name = "local"

    def __init__(self, root: str, max_mb: int = 200):
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max_mb * 1024 * 1024

    def healthy(self) -> bool:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            return os.access(self.root, os.W_OK)
        except OSError:
            return False

    def _dir(self, ref: str) -> Path:
        tenant, _, rec = ref.partition("/")
        if not (_SAFE.fullmatch(tenant) and _SAFE.fullmatch(rec) and tenant not in (".", "..") and rec not in (".", "..")):
            raise ValueError("invalid storage reference")
        d = (self.root / tenant / rec).resolve()
        if self.root not in d.parents:
            raise ValueError("invalid storage reference")
        return d

    def open(self, tenant_id: str, recording_id: str):
        if not self.healthy():
            raise RecordingUnavailable("the recordings directory is not writable")
        ref = f"{tenant_id}/{recording_id}"
        try:
            d = self._dir(ref)
            d.mkdir(mode=0o700, parents=True, exist_ok=False)
        except (OSError, ValueError) as e:
            raise RecordingUnavailable(f"could not create the recording ({type(e).__name__})") from None
        return ref, _LocalHandle(d, self.max_bytes)

    def delete(self, ref: str) -> None:
        d = self._dir(ref)
        if d.exists():
            shutil.rmtree(d)                                      # only ever a directory this provider created under its own root

    def exists(self, ref: str) -> bool:
        try:
            return self._dir(ref).exists()
        except ValueError:
            return False

    def track_path(self, ref: str, track: str):
        if track not in TRACKS:
            return None
        p = self._dir(ref) / f"{track}.wav"
        return p if p.is_file() else None


def get_provider(s) -> RecordingProvider:
    if s.trial_recording_provider == "local":
        return LocalProvider(s.trial_recordings_dir, s.trial_recording_max_mb)
    return NullProvider()


class CallRecorder:
    """What a LiveCall feeds while (and only while) consent is granted. `caller`/`ai`/`staff` are synchronous and raise if storage fails: the live call then drops
    the recorder (fail closed) and calls `fail`. `stop`/`fail` finalise the file(s) and the metadata row, and are idempotent."""

    def __init__(self, recording_id: str, handle: Handle, ai_rate: int, finish):
        self.id, self.handle, self.ai_rate, self._finish, self._done = recording_id, handle, ai_rate, finish, False

    def caller(self, pcm: bytes) -> None:
        self.handle.write("caller", pcm, 16000)

    def ai(self, frame: bytes) -> None:
        self.handle.write("assistant", frame[8:], self.ai_rate)          # frame = uint32 turn, uint32 seq, then PCM16

    def staff(self, pcm: bytes) -> None:
        self.handle.write("staff", pcm, 16000)

    async def stop(self, reason: str = "stopped") -> None:
        if self._done:
            return
        self._done = True
        size = self.handle.close()
        await self._finish("completed", reason, size)

    async def fail(self, reason: str) -> None:
        if self._done:
            return
        self._done = True
        self.handle.abort()
        await self._finish("failed", reason, 0)


# ---- metadata rows --------------------------------------------------------------------------------------------------------------------
def new_recording_id() -> str:
    import secrets
    return "rec_" + secrets.token_hex(8)


def create_row(db, tenant_id: str, call_id: str, recording_id: str, provider: str, ref: str, policy_version: int) -> None:
    from app.calls.state import now
    from app.db.models import CallRecording
    db.add(CallRecording(tenant_id=tenant_id, id=recording_id, call_id=call_id, consent_policy_version=policy_version, status="recording", provider=provider,
                         storage_ref=ref, started_at=now(), size_bytes=0, legal_hold=False, purge_attempts=0))


def finish_row(db, tenant_id: str, recording_id: str, status: str, reason: str, size: int, retention_days: int) -> None:
    """Close the metadata row of a recording (`completed` or `failed`) and set when it expires. Only a row still `recording` can be finished."""
    from datetime import timedelta
    from sqlalchemy import update
    from app.calls.state import now
    from app.db.models import CallRecording
    stamp = now()
    db.execute(update(CallRecording).where(CallRecording.tenant_id == tenant_id, CallRecording.id == recording_id, CallRecording.status == "recording")
               .values(status=status, stop_reason=reason[:32], stopped_at=stamp, size_bytes=min(size, 2**31 - 1), expires_at=stamp + timedelta(days=retention_days))
               .execution_options(synchronize_session=False))
