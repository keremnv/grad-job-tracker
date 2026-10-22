import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from jobwatch.config import load_config, watchlist

BASE = {'employers': [
    {'company': 'Example', 'provider': 'lever', 'board': 'example', 'enabled': True, 'careers_url': 'https://example.org/careers',
     'validation_url': 'https://example.org/careers', 'mapping_verified_at': '2026-10-17T00:00:00Z',
     'careers_verified_at': '2026-10-17T00:00:00Z', 'verification_notes': 'synthetic'},
    {'company': 'Example EU', 'provider': 'greenhouse', 'board': 'example-eu', 'region': 'eu', 'enabled': True,
     'careers_url': 'https://example.eu/careers', 'validation_url': 'https://example.eu/careers',
     'mapping_verified_at': '2026-10-17T00:00:00Z', 'verification_notes': 'synthetic'},
    {'company': 'Manual Co', 'provider': 'manual', 'enabled': False, 'careers_url': 'https://manual.example/careers',
     'watch_status': 'unverified', 'verification_notes': 'synthetic'}]}


class ConfigTests(unittest.TestCase):
    def test_dated_sources_and_manual_watch_entries(self):
        # The live watchlist is checked by `jobwatch validate-data`, not by unit tests.
        with TemporaryDirectory() as d:
            path=Path(d)/'config';path.write_text(json.dumps(BASE))
            cfg=load_config(path)
        self.assertEqual({(e['provider'],e['board'],e.get('region','global')) for e in cfg['employers'] if e.get('enabled')},
                         {('lever','example','global'),('greenhouse','example-eu','eu')})
        self.assertIn('manual watch',watchlist(cfg))

    def test_enabling_unverified_mapping_fails(self):
        base=copy.deepcopy(BASE)
        for field,value in [('mapping_verified_at',None),('mapping_verified_at','2026-10-17'),('validation_url','javascript:bad'),('provider','manual'),('board','../x')]:
            cfg=copy.deepcopy(base);cfg['employers'][0][field]=value
            with TemporaryDirectory() as d:
                path=Path(d)/'config';path.write_text(json.dumps(cfg))
                with self.subTest(field=field),self.assertRaises(ValueError):load_config(path)

    def test_enabled_feed_region_is_validated(self):
        base=copy.deepcopy(BASE)
        for provider,region,ok in [('greenhouse','eu',True),('lever','eu',True),('greenhouse','us',False),('ashby','eu',False),('greenhouse','',False)]:
            cfg=copy.deepcopy(base);cfg['employers'][0].update(provider=provider,region=region)
            with TemporaryDirectory() as d:
                path=Path(d)/'config';path.write_text(json.dumps(cfg))
                with self.subTest(provider=provider,region=region):
                    if ok:load_config(path)
                    else:
                        with self.assertRaises(ValueError):load_config(path)

    def test_watchlist_escapes_and_is_not_vacancy_verification(self):
        cfg={'employers':[{'company':'<b>fake</b>', 'enabled':False,'careers_url':'https://example.org/', 'relevance_notes':'[click](javascript:bad)'}]}
        out=watchlist(cfg)
        self.assertNotIn('<b>',out); self.assertIn('unverified',out); self.assertIn('unknown',out)
