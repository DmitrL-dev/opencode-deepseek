"""Install human-only budget commands into an existing Harness profile, offline."""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import time
from .harness import yaml_codec, profile_lock


def install(home, profile, token_file, base_url='http://127.0.0.1:8000'):
    with profile_lock(home, profile):
        return _install_unlocked(home, profile, token_file, base_url)


def _install_unlocked(home, profile, token_file, base_url):
    path = Path(home) / 'profiles' / profile / 'cordis.patch.yml'
    if not path.is_file():
        raise ValueError('Initialize the Harness profile first')
    yaml = yaml_codec()
    original = path.read_bytes()
    document = yaml.load(original.decode('utf-8'))
    if not isinstance(document, list):
        raise ValueError('Unsupported Harness profile')
    identity = 'local-bridge-budget'
    matches = [row for row in document if isinstance(row, dict) and (row.get('id') == identity
               or (isinstance(row.get('insert'), list) and len(row['insert']) == 1
                   and isinstance(row['insert'][0], dict) and row['insert'][0].get('id') == identity))]
    if len(matches) > 1:
        raise ValueError('Duplicate budget plugin entries')
    row = {'id':identity, 'name':str(Path(__file__).with_name('harness_budget.mjs').resolve()),
           'config':{'baseURL':base_url, 'tokenFile':str(Path(token_file).resolve())}, 'disabled':False}
    if matches:
        document[document.index(matches[0])] = {'insert':[row]}
    else:
        document.append({'insert':[row]})
    backup = path.with_name(path.name + '.budget-backup-' + str(time.time_ns()))
    shutil.copy2(path, backup)
    backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.budget-')
    try:
        with os.fdopen(fd, 'w') as out:
            yaml.dump(document, out)
        if path.read_bytes() != original:
            raise ValueError('Harness profile changed during installation; retry after reviewing it')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path.home()/'.dsh')
    parser.add_argument('--profile', default='desktop', choices=['desktop','web','headless'])
    parser.add_argument('--token-file', type=Path, required=True)
    args = parser.parse_args()
    print(install(args.home, args.profile, args.token_file))


if __name__ == '__main__': main()
