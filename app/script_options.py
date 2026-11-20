"""Persist two script candidates; never rent or stage shots before selection."""
from . import store


class AwaitingScriptChoice(Exception):
    pass


def enabled(folder):
    return (folder / "script-choice-enabled.json").is_file()


def choose(folder, generate):
    path = folder / "script-options.json"
    packet = store.read(path) if path.is_file() else {"options": []}
    while len(packet["options"]) < 2:
        number = len(packet["options"]) + 1
        extra = ""
        if number == 2:
            first = packet["options"][0]
            extra = ("\nWrite a different complete story option for the same requested duration and title brief. "
                     "Keep the approved simple style, but use a different central event, clue and payoff. "
                     "Do not paraphrase this first option:\n" + str(first))
        value = generate(number, extra)
        packet["options"].append(value)
        store.save(path, packet)
    selected = packet.get("selected")
    if selected not in (1, 2):
        raise AwaitingScriptChoice()
    return packet["options"][selected - 1]


def select(folder, number):
    if number not in (1, 2):
        raise ValueError("Choose script 1 or 2")
    path = folder / "script-options.json"
    packet = store.read(path)
    if len(packet.get("options", [])) != 2:
        raise ValueError("Both scripts are not ready yet")
    if packet.get("selected") not in (None, number):
        raise ValueError("A different script has already been selected")
    packet["selected"] = number
    store.save(path, packet)


def script_text(value):
    beats = value.get("speech", value.get("beats", []))
    lines = [beat.get("line", beat.get("speech", "")) for beat in beats]
    return "\n".join(([value["title"]] if value.get("title") else []) + lines)
