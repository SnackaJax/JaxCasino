"""The message a game page gets when its connection is refused ("fatal"). Shared by every game so a refusal that
the page handles itself has the same shape everywhere: code "session" (sign in again) and code "crux" (the player
is on a /crux break; the page shows the crux screen with the time left)."""
import json

CRUX = "crux:"   # a problem that starts with this is a crux; the rest is when it ends (unix time)


def crux_problem(until: float) -> str:
    return f"{CRUX}{until}"


def fatal_text(problem: str) -> str:
    if problem.startswith(CRUX):
        until = float(problem[len(CRUX):])
        return json.dumps({"type": "fatal", "code": "crux", "until": until,
                           "message": "You have a crux: you're taking a break from the casino."})
    return json.dumps({"type": "fatal", "message": problem,
                       "code": "session" if "session" in problem.lower() else ""})
