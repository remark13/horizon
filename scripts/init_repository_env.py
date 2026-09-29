#!/usr/bin/env python3
"""Create a local starter .env without printing its generated DB password."""
from pathlib import Path
import secrets


def initialize(root: Path) -> Path:
    template = (root / '.env.repository.example').read_text(encoding='utf-8')
    if template.count('\nPOSTGRES_PASSWORD=\n') != 1:
        raise ValueError('Expected exactly one empty POSTGRES_PASSWORD in the template.')
    content = template.replace('\nPOSTGRES_PASSWORD=\n', '\nPOSTGRES_PASSWORD=' + secrets.token_hex(24) + '\n')
    target = root / '.env'
    # Existing settings and symlink targets must never be overwritten.
    with target.open('x', encoding='utf-8') as stream:
        target.chmod(0o600)
        stream.write(content)
    return target


if __name__ == '__main__':
    path = initialize(Path(__file__).resolve().parents[1])
    print(f'Created {path.name} with a random local database password. Do not commit this file.')
