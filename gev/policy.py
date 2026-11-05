"""The project's line: public, aggregate sensor data only; never people."""
import re

POLICY = ("God's Eye View tools cover public aircraft, vessel, satellite, hazard, weather and "
          "infrastructure data. Do not use them to search for a named person, recognise faces, "
          "or track or locate an individual.")

EXCLUDED_TOOLS = {"panel_request"}  # MCP-Apps panel plumbing, not useful as a model tool

_PERSON = re.compile(
    r"\b(facial|face (recognition|match|search)|recogni[sz]e (a |the )?(face|person)|"
    r"(find|locate|track|follow) (a |the )?(person|individual|someone))\b", re.IGNORECASE)


def refuse_reason(arguments: dict) -> str | None:
    text = " ".join(str(v) for v in arguments.values() if isinstance(v, (str, int, float)))
    if _PERSON.search(text):
        return "Refused: God's Eye View does not search for people, recognise faces, or track individuals."
    return None
