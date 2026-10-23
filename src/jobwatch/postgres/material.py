"""Provider-specific material comparison of complete parsed records, never parser outputs."""
from html import unescape
from html.parser import HTMLParser
from hashlib import sha256

from .evidence import encode_evidence

COMPARISON_VERSION = 'material-v1'
MISSING = object()


class _Text(HTMLParser):
    BLOCKS = {'p', 'div', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'table', 'tr', 'td', 'section'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in self.BLOCKS:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        if tag in self.BLOCKS:
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def words(value):
    if not isinstance(value, str):
        return value
    parser = _Text()
    parser.feed(unescape(unescape(value)))
    parser.close()
    return ' '.join(''.join(parser.parts).split())


def path(record, name):
    value = record
    for part in name.split('.'):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def as_set(value):
    # Original order stays in evidence; duplicates/order are not set-like information.
    if not isinstance(value, list):
        return value
    unique = {encode_evidence({'value': v}): v for v in value}
    return [unique[k] for k in sorted(unique)]


def material(provider, raw):
    result = {}

    def take(name, source=None, transform=lambda v: v):
        value = path(raw, source or name)
        if value is not MISSING:
            result[name] = transform(value)

    def text(name, plain, html):
        value = path(raw, plain)
        if value is not MISSING:
            result[name] = ' '.join(value.split()) if isinstance(value, str) else value
        else:
            value = path(raw, html)
            if value is not MISSING:
                result[name] = words(value)

    if provider == 'greenhouse':
        for field in ('title', 'absolute_url', 'location.name', 'application_deadline'):
            take(field)
        take('description', 'content', words)
        for field in ('offices', 'departments'):
            value = path(raw, field)
            if value is not MISSING:
                selected = [{k: item[k] for k in ('id', 'name', 'location') if k in item}
                            if isinstance(item, dict) else item for item in value] if isinstance(value, list) else value
                result[field] = as_set(selected)
        take('metadata', transform=as_set)
    elif provider == 'lever':
        for field in ('text', 'hostedUrl', 'applyUrl', 'country', 'workplaceType',
                      'categories.location', 'categories.commitment', 'categories.department', 'categories.team', 'salaryRange'):
            take(field)
        for field in ('categories.allLocations', 'allLocations'):
            take(field, transform=as_set)
        for name, plain, html in (('opening', 'openingPlain', 'opening'),
                ('description', 'descriptionPlain', 'description'), ('body', 'descriptionBodyPlain', 'descriptionBody'),
                ('additional', 'additionalPlain', 'additional'), ('salary_text', 'salaryDescriptionPlain', 'salaryDescription')):
            text(name, plain, html)
        value = path(raw, 'lists')
        if value is not MISSING:
            result['lists'] = [{k: words(item[k]) if k == 'content' else item[k] for k in ('text', 'content') if k in item}
                               if isinstance(item, dict) else item for item in value] if isinstance(value, list) else value
    elif provider == 'ashby':
        for field in ('title', 'jobUrl', 'applyUrl', 'location', 'address', 'isRemote', 'workplaceType',
                      'employmentType', 'department', 'team', 'isListed'):
            take(field)
        take('description', 'descriptionPlain', lambda v: ' '.join(v.split()) if isinstance(v, str) else v)
        take('secondaryLocations', transform=as_set)
        take('compensation.compensationTierSummary')
        tiers = path(raw, 'compensation.compensationTiers')
        if tiers is not MISSING:
            result['compensation.compensationTiers'] = as_set([
                {**tier, 'components': as_set(tier['components'])} if isinstance(tier, dict) and 'components' in tier else tier
                for tier in tiers] if isinstance(tiers, list) else tiers)
        take('compensation.summaryComponents', transform=as_set)
    else:
        raise ValueError('Unsupported material-comparison provider')
    return result


def describe(value):
    """Small JSONB-safe event details; full wording always lives in referenced evidence."""
    if value is MISSING:
        return {'present': False}
    blob = encode_evidence({'value': value})
    if len(blob) > 1024 or b'\\u0000' in blob:
        return {'present': True, 'sha256': sha256(blob).hexdigest(), 'bytes': len(blob)}
    return {'present': True, 'value': value}


def compare(provider, before, after, before_request, after_request):
    old, new = material(provider, before), material(provider, after)
    changed, baseline = {}, {}
    for field in sorted(old.keys() | new.keys()):
        a, b = old.get(field, MISSING), new.get(field, MISSING)
        if a is b or (a is not MISSING and b is not MISSING and encode_evidence({'v': a}) == encode_evidence({'v': b})):
            continue
        detail = {'before': describe(a), 'after': describe(b)}
        if a is MISSING or (b is MISSING and before_request != after_request):
            baseline[field] = detail
        else:
            changed[field] = detail
    return changed, baseline
