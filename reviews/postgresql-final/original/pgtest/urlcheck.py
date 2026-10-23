import sys, subprocess
sys.path.insert(0, sys.argv[1] + '/src')
from jobwatch.identity import loose_url
urls = ['https://Jobs.Lever.co/palantir/abc', 'https://jobs.lever.co/palantir/abc/', 'https://example.com', 'https://example.com:443/x',
        'https://example.com/a?b=1#c', 'https://example.com/a#c?d', 'https://EXAMPLE.com/Path?Q=A', 'https://example.com?x=1']
for u in urls:
    py = loose_url(u)
    sql = subprocess.check_output(['docker','exec','jw-review-pg','psql','-U','postgres','-Atc',f"select jobwatch.loose_url($q${u}$q$)"]).decode().strip()
    print('SAME' if py == sql else 'DIFF', u, '| py:', py, '| sql:', sql)
