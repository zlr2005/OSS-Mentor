"""执行预生成的有界公开事件 SELECT；最多四个并发，只写新本地产物。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request


def run(directory):
    os.environ.pop('SSLKEYLOGFILE', None)
    queue = json.loads((directory / 'query_queue.json').read_text(encoding='utf-8'))
    def fetch(row):
        sql = row['sql']
        if not re.fullmatch(r'batch_\d+', row['name']) or not sql.startswith('SELECT ') or ';' in sql or 'FROM github_events' not in sql or not sql.endswith('LIMIT 200 FORMAT JSON'):
            raise ValueError('Unexpected query')
        path = directory / (row['name'] + '.json')
        if path.exists():
            return dict(name=row['name'], status='cached')
        try:
            url = 'https://play.clickhouse.com/?user=play&query=' + urllib.parse.quote(sql)
            with urllib.request.urlopen(url, timeout=40) as response:
                body = response.read().decode('utf-8')
            payload = json.loads(body)
            if not isinstance(payload.get('data'), list):
                raise ValueError('Unexpected payload')
            path.write_text(body, encoding='utf-8')
            result = dict(name=row['name'], status='read', rows=len(payload['data']),
                          possibly_truncated=len(payload['data']) >= 200,
                          observed_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            result = dict(name=row['name'], status='failed', error_type=type(error).__name__)
            if isinstance(error,urllib.error.HTTPError):
                result['http_status']=error.code
                result['detail']=error.read(1200).decode('utf-8',errors='replace')
        print(json.dumps({k:v for k,v in result.items() if k!='detail'}), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, queue))
    (directory / 'fetch_results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    run(p.parse_args().directory)
