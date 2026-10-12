"""Install local Harness shell policy without touching sessions or accounts."""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import time
from .harness import profile_lock, render, yaml_codec

IDENTITY = 'local-bridge-shell-safety'


def patch_document(document):
    if not isinstance(document, list):
        raise ValueError('Unsupported Harness profile')
    row = {'insert': [{'id': IDENTITY, 'name': str(
        Path(__file__).with_suffix('.mjs').resolve()), 'disabled': False}]}
    matches = [i for i, item in enumerate(document) if isinstance(item, dict) and
               (item.get('id') == IDENTITY or any(isinstance(child, dict) and
                child.get('id') == IDENTITY for child in item.get('insert', []) or []))]
    if len(matches) > 1:
        raise ValueError('Duplicate shell safety entries')
    result = list(document)
    if matches:
        old = result[matches[0]]
        if 'insert' in old and len(old['insert']) != 1:
            raise ValueError('Shell safety shares an insert block; preserve it manually')
        result[matches[0]] = row
    else:
        result.append(row)
    return result


def install(home, profile):
    with profile_lock(home, profile):
        path = Path(home) / 'profiles' / profile / 'cordis.patch.yml'
        original = path.read_bytes()
        updated = render(patch_document(yaml_codec().load(original.decode())))
        if updated == original:
            return path
        backup = path.with_name(path.name + '.shell-backup-' + str(time.time_ns()))
        shutil.copy2(path, backup)
        backup.chmod(0o600)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.shell-')
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(updated)
            if path.read_bytes() != original:
                raise ValueError('Harness profile changed during installation')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home() / '.dsh')
    parser.add_argument('--profile', default='desktop', choices=['desktop', 'web', 'headless'])
    args = parser.parse_args()
    print(install(args.home, args.profile))
