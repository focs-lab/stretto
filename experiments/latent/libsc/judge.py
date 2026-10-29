"""What notify2 printed: "ok" when rank 0 printed "notify2: OK", else the first FAIL line
(or "no verdict" when the run printed none: it hung or stopped before the check)."""
import re


def judge(stdout: str) -> str:
    if "notify2: OK" in stdout:
        return "ok"
    m = re.search(r"notify2: FAIL rank (\d+) call (\d+)", stdout)
    if m:
        return f"wrong rank {m.group(1)} call {m.group(2)}"
    if "notify2: FAIL" in stdout:
        return "wrong"
    return "no verdict"
