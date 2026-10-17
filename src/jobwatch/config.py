"""Source identity validation; a careers page is not a verified vacancy."""
from datetime import datetime
import json
from pathlib import Path
from .feeds import BOARD, public_url
from .render import text, link


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('verification time must be an explicit timezone-aware timestamp')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('verification timestamp needs a timezone')
    return dt


def load_config(path: Path):
    cfg = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(cfg, dict) or not isinstance(cfg.get('employers'), list):
        raise ValueError('config needs employers array')
    boards, companies = set(), set()
    for e in cfg['employers']:
        if not isinstance(e, dict) or not isinstance(e.get('company'), str) or not e['company'].strip():
            raise ValueError('invalid employer entry')
        if e['company'].casefold() in companies:
            raise ValueError('duplicate employer')
        companies.add(e['company'].casefold())
        if type(e.get('enabled', False)) is not bool:
            raise ValueError('enabled must be boolean')
        if not public_url(e.get('careers_url', '')):
            raise ValueError('careers URL must be public HTTPS')
        if e.get('careers_verified_at'):
            timestamp(e['careers_verified_at'])
        if e.get('watch_status', 'unverified') not in {'open', 'register_interest', 'opening_later', 'listed_in_feed', 'unverified'}:
            raise ValueError('unknown watch status')
        if e.get('enabled'):
            if e.get('provider') not in {'greenhouse', 'lever', 'ashby'} or not BOARD.fullmatch(e.get('board', '')):
                raise ValueError('enabled feed requires supported provider and board slug')
            if e.get('region', 'global') not in ({'global', 'eu'} if e['provider'] != 'ashby' else {'global'}):
                raise ValueError('feed region must be explicit global or eu (Greenhouse/Lever only)')
            if not public_url(e.get('validation_url', '')):
                raise ValueError('enabled feed requires official validation URL')
            timestamp(e.get('mapping_verified_at'))
            key = (e['provider'], e['board'].lower())
            if key in boards:
                raise ValueError('duplicate configured board')
            boards.add(key)
    return cfg


def watchlist(cfg):
    lines=['# Employer watchlist', '', 'A verified careers URL does not verify an opening or eligibility. Only enabled ATS boards are collected. Check manual sources throughout 2027.', '']
    for e in cfg['employers']:
        mode = f"API: {e['provider']}/{e['board']}" if e.get('enabled') else 'manual watch'
        lines += [f"- **{text(e['company'])}** — {link('official careers', e['careers_url'])}; {text(mode)}.",
                  f"  Careers source verified: {text(e.get('careers_verified_at', 'unknown'))}; vacancy watch status: {text(e.get('watch_status', 'unverified'))}.",
                  f"  {text(e.get('relevance_notes', 'Role suitability unknown'))}"]
    return '\n'.join(lines)+'\n'
