"""Find CLI tools installed alongside the Python running the controller."""
import os
from pathlib import Path
import sys
import sysconfig


def tool_environment(environ):
    env = dict(environ)
    path_keys = [key for key in env if key.upper() == 'PATH']
    existing = env.get('PATH', env.get(path_keys[0], '') if path_keys else '')
    directories = [str(Path(sys.executable).resolve().parent), sysconfig.get_path('scripts')]
    entries = []
    seen = set()
    for entry in [*directories, *existing.split(os.pathsep)]:
        if not entry:
            continue
        identity = os.path.normcase(os.path.abspath(entry))
        if identity not in seen:
            entries.append(entry)
            seen.add(identity)
    if os.name == 'nt':
        for key in path_keys:
            env.pop(key)
    env['PATH'] = os.pathsep.join(entries)
    return env
