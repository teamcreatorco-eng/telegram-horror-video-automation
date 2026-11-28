"""Start or reopen the localhost controller. Never launch generation directly."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError
import webbrowser
from dotenv import dotenv_values
from .runtime_tools import tool_environment


def settings(root, environ=None):
    root=Path(root).resolve()
    env={**dotenv_values(root/'.env'),**dotenv_values(root/'.env.telegram'),
         **(os.environ if environ is None else environ)}
    env={str(k):str(v) for k,v in env.items() if v is not None}
    env=tool_environment(env)
    port=int(env.get('PORT','8000'))
    if not 1<=port<=65535:raise ValueError('PORT must be between 1 and 65535')
    data=Path(env.get('DATA_DIR') or root/'data')
    if not data.is_absolute():data=root/data
    data=data.resolve()
    identity=hashlib.sha256(f'{root}|{data}'.encode()).hexdigest()[:24]
    env.update(HOST='127.0.0.1',PORT=str(port),APP_ROLE='web',DATA_DIR=str(data),
               RAGHU_RESUME_ONCE='',RAGHU_PRODUCE_PLAN_ONCE='',NIGHTFALL_LOCAL_INSTANCE=identity)
    return dict(root=root,data=data,identity=identity,url=f'http://127.0.0.1:{port}',env=env)


def health(url):
    try:
        with urlopen(url+'/healthz',timeout=2) as response:
            return json.load(response)
    except (OSError,URLError,ValueError):return None


def process_alive(pid):
    return process_fingerprint(pid) is not None


def process_fingerprint(pid):
    """Read process creation identity; os.kill(pid, 0) is destructive on Windows."""
    if type(pid) is not int or pid<=0:return None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        handle=kernel.OpenProcess(0x1000,False,pid) # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:return None
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
            creation,exited,kernel_time,user_time=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*map(ctypes.byref,(creation,exited,kernel_time,user_time))):return None
            return f'{pid}:{creation.dwHighDateTime}:{creation.dwLowDateTime}'
        finally:kernel.CloseHandle(handle)
    try:
        stat=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return f'{pid}:{stat[19]}' # start time, field 22
    except (OSError,IndexError):return None


@contextmanager
def startup_lock(folder):
    path=folder/'startup.lock'
    with path.open('a+b') as lock:
        if path.stat().st_size==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('The application is already starting. Wait a few seconds and open it again.') from None
        try:yield
        finally:
            lock.seek(0)
            if os.name=='nt':msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)


def start(config):
    folder=config['data']/'local-host';folder.mkdir(parents=True,exist_ok=True)
    with startup_lock(folder):
        state=health(config['url'])
        if state is not None:
            if not isinstance(state,dict) or state.get('local_instance')!=config['identity']:
                raise RuntimeError('This port is occupied by another application. Set a different PORT in .env.')
        else:
            path=folder/'process.json'
            try:previous=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):previous={}
            current=process_fingerprint(previous.get('pid'))
            if (previous.get('identity')==config['identity'] and previous.get('fingerprint')
                    and previous['fingerprint']==current):
                raise RuntimeError('The local controller is running but is not ready. Check data/local-host/server.log; a second bot was not started.')
            with (folder/'server.log').open('ab') as log:
                options=dict(cwd=str(config['root']),env=config['env'],stdin=subprocess.DEVNULL,
                             stdout=log,stderr=subprocess.STDOUT)
                if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
                else:options['start_new_session']=True
                child=subprocess.Popen([sys.executable,'run.py'],**options)
            partial=path.with_suffix('.tmp')
            partial.write_text(json.dumps(dict(pid=child.pid,identity=config['identity'],
                                              fingerprint=process_fingerprint(child.pid))),encoding='utf-8')
            partial.replace(path)
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                if child.poll() is not None:
                    raise RuntimeError('The controller could not start. Check data/local-host/server.log and complete onboarding.')
                state=health(config['url'])
                if isinstance(state,dict) and state.get('local_instance')==config['identity']:break
                time.sleep(.5)
            else:
                raise RuntimeError('Startup is taking longer than expected. Check data/local-host/server.log; do not start another copy.')
    url=config['url']+'/healthz'
    webbrowser.open(url)
    return url


def main():
    try:
        url=start(settings(Path(__file__).resolve().parent.parent))
        print('Local controller is ready: '+url+'\nUse your Telegram bot to generate or resume a video.')
    except (OSError,ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
