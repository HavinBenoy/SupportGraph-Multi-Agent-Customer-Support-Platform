"""Small statistics helpers (no numpy needed)."""

import hashlib
import math
import platform
from pathlib import Path


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarise(values: list[float], digits: int = 3) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    r = lambda x: round(x, digits)  # noqa: E731
    return {"n": len(vals), "mean": r(sum(vals) / len(vals)), "p50": r(percentile(vals, 50)),
            "p95": r(percentile(vals, 95)), "min": r(min(vals)), "max": r(max(vals))}


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def system_info() -> dict:
    """Hardware facts relevant to model speed. No hostname, username or paths."""
    info: dict = {"os": platform.system() + " " + platform.release(), "python": platform.python_version(),
                  "machine": platform.machine(), "cpu_model": None}
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        import psutil

        info.update(cpu_cores_physical=psutil.cpu_count(logical=False), cpu_threads=psutil.cpu_count(logical=True),
                    ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
                    ram_available_gb=round(psutil.virtual_memory().available / 2**30, 1))
        load = psutil.getloadavg()
        info["load_avg_1m"] = round(load[0], 2)  # high before a run = noisy numbers
    except ImportError:
        info["note"] = "psutil not installed: RAM/cores omitted (pip install psutil)"
    return info


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson interval for k successes out of n. Shows how little a difference means at these sample sizes."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - a) / d, 3), round((c + a) / d, 3)]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())
