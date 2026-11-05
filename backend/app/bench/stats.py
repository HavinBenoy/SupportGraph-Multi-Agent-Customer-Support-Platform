"""Small statistics helpers (no numpy needed)."""

import math
import platform


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
