"""Persist two script candidates; never rent or stage shots before selection."""
from . import store, storage
import re


class AwaitingScriptChoice(Exception):
    pass


def enabled(folder):
    return (folder / "script-choice-enabled.json").is_file()

def job_for(folder):
    if re.fullmatch(r'[a-f0-9]{32}', folder.name) and folder.parent.resolve()==store.DATA.resolve():
        return store.get(folder.name)
    return None

def read_options(folder):
    if storage.enabled() and job_for(folder):
        value=storage.read_json(folder.name,'script-options.json')
        if value is not None:return value
    path=folder/'script-options.json'
    return store.read(path) if path.is_file() else {'options':[]}


def choose(folder, generate):
    path = folder / "script-options.json"
    packet = read_options(folder)
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
    job=job_for(folder)
    selected = (job.get('script_choice') if job else None) or packet.get("selected")
    if selected not in (1, 2):
        raise AwaitingScriptChoice()
    return packet["options"][selected - 1]


def select(folder, number):
    if number not in (1, 2):
        raise ValueError("Choose script 1 or 2")
    path = folder / "script-options.json"
    packet = read_options(folder)
    if len(packet.get("options", [])) != 2:
        raise ValueError("Both scripts are not ready yet")
    job=job_for(folder)
    selected=(job.get('script_choice') if job else None) or packet.get('selected')
    if selected not in (None, number):
        raise ValueError("A different script has already been selected")
    if job:
        # Shared DB is authoritative; a worker may later publish an older local JSON copy.
        store.select_script(folder.name,number)
        return
    packet["selected"] = number
    store.save(path, packet)


def script_text(value):
    beats = value.get("speech", value.get("beats", []))
    lines = [beat.get("line", beat.get("speech", "")) for beat in beats]
    return "\n".join(([value["title"]] if value.get("title") else []) + lines)
