from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from tqdm import tqdm


def main() -> None:
    p = argparse.ArgumentParser(description='Import ExtractedDocument JSONL into Scientific Knot backend.')
    p.add_argument('jsonl_path')
    p.add_argument('--backend-url', default='http://localhost:8000')
    p.add_argument('--user-id', default='ml_importer')
    p.add_argument('--role', default='admin')
    p.add_argument('--token', default='admin-token')
    p.add_argument('--timeout', type=float, default=180.0)
    args = p.parse_args()

    path = Path(args.jsonl_path)
    url = args.backend_url.rstrip('/') + '/api/documents/import-extracted'
    params = {'user_id': args.user_id, 'role': args.role}
    headers = {'X-Demo-Role-Token': args.token, 'Content-Type': 'application/json'}

    success = failed = 0
    with httpx.Client(timeout=args.timeout) as client, path.open('r', encoding='utf-8') as f:
        lines = [line for line in f if line.strip()]
        for line_no, line in enumerate(tqdm(lines, desc='Importing documents'), start=1):
            try:
                payload = json.loads(line)
                response = client.post(url, params=params, headers=headers, json=payload)
                response.raise_for_status()
                success += 1
            except Exception as exc:
                failed += 1
                print(f'[ERROR] line {line_no}: {type(exc).__name__}: {exc}')
    print(f'Success: {success}')
    print(f'Failed:  {failed}')


if __name__ == '__main__':
    main()
