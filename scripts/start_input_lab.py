"""Start the prepared local lab and open its workbench (no installation)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen
import webbrowser

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=4185)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for version in ('baseline', 'optimized'):
        if not (root / 'web' / version / 'dist' / 'index.html').is_file():
            raise RuntimeError(f'Build web/{version} first. See docs/integration/RUN.md.')
    url = f'http://127.0.0.1:{args.port}'
    expected = {'service': 'jammate-reference-workbench-v1', 'root': str(root)}
    occupied = f'Port {args.port} is already serving another workbench. Stop it or choose a different --port.'

    def ready():
        try:
            with urlopen(f'{url}/api/ready', timeout=1) as response:
                service = json.loads(response.read(4096))
        except (HTTPError, ValueError) as exc:
            raise RuntimeError(occupied) from exc
        except OSError:
            return False
        if service != expected:
            raise RuntimeError(occupied)
        return True
    if not ready():
        logs = root / 'tmp'
        logs.mkdir(exist_ok=True)
        with (logs / 'input-server.log').open('ab') as out, (logs / 'input-server-errors.log').open('ab') as err:
            process = subprocess.Popen([sys.executable, '-m', 'jammate.input_server', '--port', str(args.port)],
                cwd=root, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(100):
            if ready():
                break
            if process.poll() is not None:
                raise RuntimeError('Server exited. Check tmp/input-server-errors.log for an occupied port or missing dependency.')
            time.sleep(.2)
        else:
            raise RuntimeError('Server startup timed out. Check tmp/input-server-errors.log.')
    print(url)
    if not args.no_browser:
        webbrowser.open(url)

if __name__ == '__main__':
    main()
