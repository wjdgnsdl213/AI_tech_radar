"""Read-only smoke check for shared exploration and export filters."""
import argparse
import csv
import io

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8021')
    args = parser.parse_args()
    params = {'q': '모두의 AI', 'since': '2026-09-01', 'until': '2026-09-11', 'size': 200}
    with httpx.Client(base_url=args.base, timeout=45) as client:
        relevance = client.get('/api/search', params={**params, 'order': 'relevance'})
        timeline = client.get('/api/search', params={**params, 'order': 'oldest'})
        relevance.raise_for_status()
        timeline.raise_for_status()
        a, b = relevance.json(), timeline.json()
        assert a['total'] == b['total']
        for result in (a, b):
            assert all(params['since'] <= row['published'] <= params['until'] for row in result['items'])
        assert [r['published'] for r in b['items']] == sorted(r['published'] for r in b['items'])
        if a['total'] <= params['size']:
            assert {r['id'] for r in a['items']} == {r['id'] for r in b['items']}
        print(f"Article/timeline period parity: {a['total']} results")
        response = client.get('/search.csv', params=params)
        response.raise_for_status()
        rows = list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
        assert all(params['since'] <= row[0] <= params['until'] for row in rows[1:])
        assert len(rows) - 1 == min(a['total'], 2000)
        print(f'CSV KST dates: {len(rows) - 1} results')
        response = client.get('/api/keyword/소상공인', params={'since': params['since'], 'until': params['until'], 'limit': 3})
        response.raise_for_status()
        data = response.json()
        assert all(params['since'] <= row['published'] <= params['until'] for row in data['items'])
        print(f"Keyword evidence: {len(data['items'])} displayed / {data['total']} in selected period")
        assert client.get('/api/search', params={'order': 'invalid'}).status_code == 422
    print('Navigation API checks passed; no model calls or data writes.')


if __name__ == '__main__':
    main()
