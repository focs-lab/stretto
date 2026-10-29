"""What adlbsub printed: "ok" for "adlbsub: OK", "server error" when a server's loop failed
(ADLB prints "handle(): invalid tag" before it), "lost tasks" for FAIL, else "no verdict"."""


def judge(stdout: str) -> str:
    if "adlbsub: OK" in stdout:
        return "ok"
    if "SERVER ERROR" in stdout:
        return "server error"
    if "adlbsub: FAIL" in stdout:
        return "lost tasks"
    return "no verdict"
