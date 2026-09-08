"""Prepare and capture two real Inkscape desktops from the same checkpoint."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree

from dotenv import load_dotenv
from fork.cu import db
from fork.repl_client import ReplClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def make(branch, count):
    colors = ['#bba6ea', '#83d5bc', '#89b6e8']
    circles = ''.join(f'<ellipse cx="450" cy="300" rx="200" ry="85" transform="rotate({i * 60} 450 300)" stroke="{colors[i]}" stroke-width="14" fill="none"/>' for i in range(count))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="660"><rect width="900" height="660" fill="#13151c"/>{circles}<circle cx="450" cy="300" r="18" fill="#f0e8ff"/><text x="450" y="545" text-anchor="middle" font-family="DejaVu Sans" font-size="65" fill="#f2ecfa">multiverse</text><text x="450" y="595" text-anchor="middle" font-family="DejaVu Sans" font-size="20" letter-spacing="5" fill="#b0a8bb">EXPLORE EVERY PATH</text></svg>'
    with ReplClient(f"http://127.0.0.1:{db.get_branch(branch).ports['repl']}") as repl:
        code = f"from pathlib import Path\nPath('/home/user/multiverse.svg').write_text({svg!r})\nsubprocess.Popen(['inkscape','/home/user/multiverse.svg'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\ntime.sleep(3)\nsubprocess.run(['wmctrl','-r',':ACTIVE:','-b','add,maximized_vert,maximized_horz'])\npyautogui.press('5')\ntime.sleep(1)"
        repl.call('exec_py', code=code, timeout_ms=15000)
        observed = repl.call('observe', mode='both', target='desktop', max_tree_chars=2000000)
        (ROOT / 'video/assets' / f'{branch}.png').write_bytes(base64.b64decode(observed['screenshot']))
        readback = repl.call('exec_py', code="log(Path('/home/user/multiverse.svg').read_text())", timeout_ms=5000)
    actual = readback.get('stdout', '')
    root = ElementTree.fromstring(actual.strip())
    ellipses = len(root.findall('{http://www.w3.org/2000/svg}ellipse'))
    return {'branch': branch, 'orbit_paths': ellipses, 'required': 3, 'pass': ellipses == 3,
            'source': 'scripted native-app demonstration', 'screenshot': f'{branch}.png'}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: make(*args), [('demo-vector-a', 3), ('demo-vector-b', 2)]))
    (ROOT / 'video/assets/native-results.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results))
