"""Optional timing events for one worker invocation; no command arguments are logged."""
import sys
import time
from common import event

if __name__ == '__main__':
    action, phase, ident, started = sys.argv[1:5]
    if action == 'start':
        event('worker_phase_start', phase=phase, phase_id=ident)
    else:
        event('worker_phase_done', phase=phase, phase_id=ident,
              seconds=max(0, time.monotonic()-float(started)), exit_code=int(sys.argv[5]))
