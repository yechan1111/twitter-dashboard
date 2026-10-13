"""Run once on your Mac: python3 install_mac.py. No administrator access needed."""
import getpass
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = 'com.signal.kol-dashboard'
ROOT = Path(__file__).resolve().parent
SERVICE = 'SignalTwitterAPI'


def launch_config(root, python, home):
    logs = home / 'Library' / 'Logs' / 'SignalDashboard'
    return {'Label': LABEL, 'ProgramArguments': [str(python), str(root / 'server.py')],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True,
            'ThrottleInterval': 30, 'StandardOutPath': str(logs / 'server.log'),
            'StandardErrorPath': str(logs / 'error.log')}


def main():
    if sys.platform != 'darwin':
        raise SystemExit('이 설치 도구는 맥에서 실행해주세요.')
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 이상이 필요합니다.')
    print('이 폴더를 이동하거나 삭제하면 자동 실행이 중단됩니다: ' + str(ROOT))
    result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-a',
                             getpass.getuser(), '-s', SERVICE], capture_output=True)
    if result.returncode:
        print('다음 비밀번호 입력창에 재발급한 TwitterAPI.io 키를 입력하세요.')
        # With no argument, -w prompts interactively; no key in command arguments.
        result = subprocess.run(['/usr/bin/security', 'add-generic-password', '-a',
                                 getpass.getuser(), '-s', SERVICE, '-w'])
        if result.returncode:
            raise SystemExit('키 등록이 완료되지 않았습니다. 자동 실행을 설치하지 않았습니다.')
    home = Path.home()
    logs = home / 'Library' / 'Logs' / 'SignalDashboard'
    logs.mkdir(parents=True, exist_ok=True)
    target = home / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    target.parent.mkdir(parents=True, exist_ok=True)
    config = launch_config(ROOT, Path(sys.executable).resolve(), home)
    if target.exists():
        previous = plistlib.loads(target.read_bytes())
        if previous.get('ProgramArguments') != config['ProgramArguments']:
            raise SystemExit('다른 위치의 Signal 자동 실행 설정이 이미 있습니다. 기존 설정을 확인해주세요.')
    target.write_bytes(plistlib.dumps(config))
    os.chmod(target, 0o600)
    domain = 'gui/' + str(os.getuid())
    subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    result = subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(target)], capture_output=True)
    if result.returncode:
        raise SystemExit('자동 실행 등록 실패. 설정 파일은 보관했습니다: ' + str(target))
    subprocess.run(['/usr/bin/open', 'http://127.0.0.1:8000'])
    print('설치 완료. 맥 로그인 시 자동 시작하고 09시·15시·21시에 갱신합니다.')
    print('잠자기 중에는 수집이 멈추며 깨어나면 최신 슬롯을 한 번 갱신합니다.')


if __name__ == '__main__':
    main()
