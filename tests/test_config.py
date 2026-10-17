import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from jobwatch.config import load_config, watchlist


class ConfigTests(unittest.TestCase):
    def test_real_config_has_dated_sources_and_only_verified_feeds(self):
        cfg = load_config(Path('config/employers.json'))
        self.assertGreaterEqual(sum(bool(e.get('careers_verified_at')) for e in cfg['employers']),30)
        # Each mapping below was verified from the employer's own site; see verification_notes.
        self.assertEqual({(e['provider'],e['board'],e.get('region','global')) for e in cfg['employers'] if e.get('enabled')},
                         {('lever','palantir','global'),('greenhouse','monzo','global'),('ashby','incident','global'),
                          ('greenhouse','cambridgeconsultantslimited','eu'),('greenhouse','riverlane','eu'),('greenhouse','mw-tech-grad','global'),
                          ('ashby','hyperexponential','global'),('lever','scottlogic','global'),('ashby','tracebit','global')})
        # Boards seen only via search or an unreachable employer site stay manual.
        manual={e['company']:e for e in cfg['employers'] if not e.get('enabled')}
        for name in ('DV Trading','Bloxd','Fifty One Degrees (51D)','Resonance (Applied AI)'):
            self.assertNotIn('mapping_verified_at',manual[name]); self.assertNotIn('careers_verified_at',manual[name])
        self.assertGreaterEqual(len(cfg['employers']),30)
        for e in cfg['employers']:
            self.assertTrue(e.get('verification_notes'))
        self.assertIn('manual watch',watchlist(cfg))

    def test_enabling_unverified_mapping_fails(self):
        base=json.loads(Path('config/employers.json').read_text())
        for field,value in [('mapping_verified_at',None),('mapping_verified_at','2026-10-17'),('validation_url','javascript:bad'),('provider','manual'),('board','../x')]:
            cfg=copy.deepcopy(base);cfg['employers'][0][field]=value
            with TemporaryDirectory() as d:
                path=Path(d)/'config';path.write_text(json.dumps(cfg))
                with self.subTest(field=field),self.assertRaises(ValueError):load_config(path)

    def test_enabled_feed_region_is_validated(self):
        base=json.loads(Path('config/employers.json').read_text())
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
