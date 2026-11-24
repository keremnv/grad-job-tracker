"""Bounded, read-only clients for documented public ATS posting APIs."""
from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPException
import json
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

BOARD = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
MAX_RESPONSE = 8 * 1024 * 1024


@dataclass(frozen=True)
class Posting:
    company: str
    provider: str
    board: str
    external_id: str
    title: str
    location: str
    url: str
    description: str = ""
    published_at: str = ""
    salary: str = ""
    country: str = ""
    countries: tuple[str, ...] = ()
    source_dates: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    # Optional ATS classification metadata used for screening. Never validated
    # strictly: a missing or oddly shaped category must not fail a board.
    department: str = ""
    team: str = ""
    employment_type: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.board.lower()}:{self.external_id}"


def request_json(url: str):
    req = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "PersonalGradJobWatch/0.1 (public job-board feeds; no applications)",
    })
    with urlopen(req, timeout=25) as response:
        if response.geturl() != url:
            raise ValueError("unexpected API redirect")
        try:
            raw = response.read(MAX_RESPONSE + 1)
        except HTTPException as exc:
            raise ValueError("incomplete HTTP response") from exc
        if len(raw) > MAX_RESPONSE:
            raise ValueError("feed response exceeds 8 MiB")
        if response.headers.get("Content-Length") and len(raw) != int(response.headers["Content-Length"]):
            raise ValueError("truncated feed response")
        return json.loads(raw.decode("utf-8"))


def public_url(value: str) -> bool:
    if not isinstance(value, str) or re.search(r"[\s\\<>\x00-\x1f\x7f]", value):
        return False
    try:
        u = urlsplit(value)
        return u.scheme == "https" and bool(u.hostname) and not u.username and not u.password and u.port in (None, 443)
    except ValueError:
        return False


def _text(value, name: str, required=False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"invalid {name}: expected {'non-empty ' if required else ''}text")
    return value


def _object(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"invalid {name}: expected object")
    return value


def _optional_object(value, name: str) -> dict:
    return _object({} if value is None else value, name)


def _list(value, name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"invalid {name}: expected array")
    return value


def _identity(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
        raise ValueError("invalid posting ID")
    return str(value)


def _link(value, kind, board, region="global") -> tuple[str, str]:
    if not public_url(value):
        raise ValueError("invalid posting URL")
    if (kind, board, region) == ("greenhouse", "riverlane", "eu"):
        # This board uses its official careers page. Accept only the observed
        # canonical query form; no decoding, extra parameters or custom-host fallback.
        match = re.fullmatch(r"https://www\.riverlane\.com/jobs\?gh_jid=([0-9]+)(?:#vacancies)?", value)
        if match:
            return value, match[1]  # collect_board also checks this against the native ID.
    u = urlsplit(value)
    parts = u.path.strip("/").split("/")
    if kind == "greenhouse":
        # EU-hosted boards are served by the same documented API but link to EU job-board hosts.
        hosts = {"boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"} if region == "eu" else {"boards.greenhouse.io", "job-boards.greenhouse.io"}
        valid = u.hostname in hosts and len(parts) == 3 and parts[:2] == [board, "jobs"]
    elif kind == "workable":
        if parts and parts[-1] == "apply":
            parts = parts[:-1]
        valid = (
            u.hostname == "apply.workable.com"
            and (
                (len(parts) == 2 and parts[0] == "j")
                or (len(parts) == 3 and parts[0].lower() == board.lower() and parts[1] == "j")
            )
        )
    else:
        host = ("jobs.eu.lever.co" if region == "eu" else "jobs.lever.co") if kind == "lever" else "jobs.ashbyhq.com"
        if parts[-1] in ("apply", "application"):
            parts = parts[:-1]
        board_match = parts[0].lower() == board.lower() if kind == "ashby" else parts[0] == board
        valid = u.hostname == host and len(parts) == 2 and board_match
    if not valid:
        raise ValueError("posting URL does not match configured provider and board; review custom hosts explicitly")
    return value, _identity(parts[-1])


def _meta(*values) -> str:
    """Join optional string metadata; ignore anything that is not text."""
    return ", ".join(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def group_workable_widget_jobs(raw_jobs: list) -> list[dict]:
    """Merge multi-location Workable widget items sharing one native shortcode.

    Workable's public widget endpoint emits one array element per country/location
    for multi-location postings (for example, UK + Ireland) with identical
    shortcode, title, and URL. Grouping preserves every location entry while
    enforcing one canonical posting per shortcode. Conflicting titles or URLs
    under the same shortcode fail closed.
    """
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in raw_jobs:
        j = _object(item, "Workable posting")
        sc = _identity(j.get("shortcode"))
        if sc not in grouped:
            grouped[sc] = []
            order.append(sc)
        grouped[sc].append(j)

    merged: list[dict] = []
    for sc in order:
        entries = grouped[sc]
        if len(entries) == 1:
            merged.append(entries[0])
            continue
        titles = {_text(e.get("title"), "title", True).strip() for e in entries}
        urls = {_text(e.get("url") or e.get("shortlink"), "url", True).strip() for e in entries}
        if len(titles) != 1 or len(urls) != 1:
            raise ValueError("conflicting duplicate Workable shortcode")
        base = dict(entries[0])
        for ent in entries:
            if isinstance(ent.get("country"), str) and ent["country"].strip().lower() in ("united kingdom", "uk", "gb"):
                base = dict(ent)
                break
        all_locations: list[dict] = []
        seen_locs: set[tuple[str, str, str, str]] = set()
        for ent in entries:
            locs = _list(ent.get("locations", []), "locations") if ent.get("locations") is not None else []
            if locs:
                for loc in locs:
                    loc = _object(loc, "location")
                    lk = (
                        _text(loc.get("country"), "location country"),
                        _text(loc.get("countryCode"), "location countryCode"),
                        _text(loc.get("region"), "location region"),
                        _text(loc.get("city"), "location city"),
                    )
                    if lk not in seen_locs:
                        seen_locs.add(lk)
                        all_locations.append(loc)
            elif ent.get("country") or ent.get("city"):
                loc = {
                    "country": _text(ent.get("country"), "country"),
                    "city": _text(ent.get("city"), "city"),
                    "region": _text(ent.get("state"), "state"),
                }
                lk = (loc["country"], "", loc["region"], loc["city"])
                if lk not in seen_locs:
                    seen_locs.add(lk)
                    all_locations.append(loc)
        base["locations"] = all_locations
        base["_widget_multi_location_entries"] = entries
        merged.append(base)
    return merged


def collect_board(entry: dict, get_json=request_json, pause=time.sleep, stats: dict | None = None) -> list[Posting]:
    """Any malformed record invalidates the board, preserving previous observations.

    `stats`, when supplied, receives counts of source records that were not
    vacancies (Greenhouse prospects, unlisted Ashby jobs) so every returned
    record can be accounted for."""
    kind, board = entry["provider"], entry.get("board")
    if not isinstance(board, str) or not BOARD.fullmatch(board):
        raise ValueError("invalid board identifier: expected a single ATS board slug")
    company = _text(entry["company"], "company", True)
    region = entry.get("region", "global")
    if kind == "greenhouse" and region not in ("global", "eu"):
        raise ValueError("Greenhouse region must be 'global' or 'eu'")
    rows = []
    if kind == "greenhouse":
        data = _object(get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"), "Greenhouse response")
        jobs = _list(data.get("jobs"), "Greenhouse jobs")
        total = _object(data.get("meta", {}), "Greenhouse meta").get("total")
        if total is not None and (type(total) is not int or total != len(jobs)):
            raise ValueError("Greenhouse total does not match jobs; incomplete response")
    elif kind == "ashby":
        data = _object(get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"), "Ashby response")
        if data.get("apiVersion", "1") != "1":
            raise ValueError("unsupported Ashby API version")
        jobs = _list(data.get("jobs"), "Ashby jobs")
    elif kind == "lever":
        if region not in ("global", "eu"):
            raise ValueError("Lever region must be 'global' or 'eu'")
        host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
        jobs = []
        for skip in range(0, 10000, 100):
            if skip:
                pause(1.0)  # api.lever.co/robots.txt: Crawl-delay: 1
            batch = _list(get_json(f"https://{host}/v0/postings/{board}?mode=json&skip={skip}&limit=100"), "Lever response")
            if len(batch) > 100:
                raise ValueError("Lever returned more records than requested")
            jobs.extend(batch)
            # Detect ignored pagination before requesting another page.
            ids = [_identity(_object(j, "Lever posting").get("id")) for j in jobs]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate posting identity or repeated Lever page")
            if len(batch) < 100:
                break
        else:
            raise ValueError("Lever pagination exceeded 10,000 postings")
    elif kind == "workable":
        if region != "global":
            raise ValueError("Workable region must be 'global'")
        data = _object(get_json(f"https://apply.workable.com/api/v1/widget/accounts/{board}?details=true"), "Workable response")
        raw_jobs = _list(data.get("jobs"), "Workable jobs")
        jobs = group_workable_widget_jobs(raw_jobs)
    else:
        raise ValueError(f"unsupported provider: {kind!r}")

    identities = set()
    skipped = 0
    for item in jobs:
        j = _object(item, "posting")
        countries, dates = [], {}
        department = team = employment_type = ""
        if kind == "greenhouse":
            if "internal_job_id" not in j:
                raise ValueError("Greenhouse record lacks internal_job_id; cannot distinguish prospects")
            if j["internal_job_id"] is None:
                skipped += 1
                continue  # documented prospect, not a vacancy
            identifier = _identity(j.get("id"))
            link, link_id = _link(j.get("absolute_url"), kind, board, region)
            title = _text(j.get("title"), "title", True)
            locations = [_text(_optional_object(j.get("location"), "location").get("name"), "location name")]
            for office in _list(j.get("offices", []), "offices"):
                office = _object(office, "office")
                locations += [_text(office.get("name"), "office name"), _text(office.get("location"), "office location")]
            description = _text(j.get("content"), "content")
            for key in ("updated_at", "first_published"):
                if j.get(key) is not None:
                    dates[key] = _text(j[key], key)
            if j.get("application_deadline") is not None:
                _text(j["application_deadline"], "application_deadline", True)
            published = dates.get("first_published", "")
            salary = ""
            departments = j.get("departments") if isinstance(j.get("departments"), list) else []
            department = _meta(*(d.get("name") for d in departments if isinstance(d, dict)))
        elif kind == "lever":
            identifier = _identity(j.get("id"))
            link, link_id = _link(j.get("hostedUrl") or j.get("applyUrl"), kind, board, region)
            title = _text(j.get("text"), "text", True)
            categories = _optional_object(j.get("categories"), "categories")
            countries.append(_text(j.get("country"), "country"))
            locations = [_text(categories.get("location"), "location")]
            # allLocations belongs inside categories in the documented response.
            locations += [_text(x, "allLocations item") for x in _list(categories.get("allLocations", []), "categories.allLocations")]
            # Preserve legacy top-level data if a board supplies it, too.
            locations += [_text(x, "allLocations item") for x in _list(j.get("allLocations", []), "allLocations")]
            description = _text(j.get("descriptionPlain"), "descriptionPlain")
            # Lists/additional text carry requirements absent from descriptionPlain.
            details = _list(j.get("lists", []), "lists")
            description += "\n" + "\n".join(_text(_object(x, "list item").get("content"), "list content") for x in details)
            description += "\n" + _text(j.get("additionalPlain"), "additionalPlain")
            if "createdAt" in j:
                if type(j["createdAt"]) is not int or j["createdAt"] < 0:
                    raise ValueError("invalid createdAt")
                dates["createdAt"] = j["createdAt"]  # not documented as publication time
            published, salary = "", ""
            department, team = _meta(categories.get("department")), _meta(categories.get("team"))
            employment_type = _meta(categories.get("commitment"))
        elif kind == "workable":
            identifier = _identity(j.get("shortcode"))
            link, link_id = _link(j.get("url") or j.get("shortlink"), kind, board, region)
            title = _text(j.get("title"), "title", True)
            locations = []
            for key in ("city", "state", "country"):
                val = _text(j.get(key), key)
                if val:
                    locations.append(val)
            top_country = _text(j.get("country"), "country")
            if top_country:
                countries.append(top_country)
            raw_locs = _list(j.get("locations", []), "locations") if j.get("locations") is not None else []
            for loc in raw_locs:
                loc = _object(loc, "location")
                loc_parts = [_text(loc.get(k), f"location {k}") for k in ("city", "region", "country")]
                joined = ", ".join(x for x in loc_parts if x)
                if joined:
                    locations.append(joined)
                for ck in ("country", "countryCode"):
                    cv = _text(loc.get(ck), f"location {ck}")
                    if cv:
                        countries.append(cv)
            if j.get("telecommuting") is True:
                locations.append("Remote")
            elif j.get("telecommuting") not in (None, False):
                raise ValueError("invalid telecommuting flag")
            description = _text(j.get("description"), "description")
            for key in ("published_on", "created_at"):
                if j.get(key) is not None:
                    val = _text(j[key], key)
                    if val:
                        dates[key] = val
            published = dates.get("published_on", "")
            salary = ""
            department = _meta(j.get("department"))
            team = _meta(j.get("function"))
            employment_type = _meta(j.get("employment_type"))
        else:
            if "isListed" in j and type(j["isListed"]) is not bool:
                raise ValueError("invalid isListed")
            if j.get("isListed") is False:
                skipped += 1
                continue
            link, identifier = _link(j.get("jobUrl") or j.get("applyUrl"), kind, board)
            link_id = identifier
            if j.get("id") is not None and _identity(j["id"]) != identifier:
                raise ValueError("Ashby source ID and hosted URL disagree")
            title = _text(j.get("title"), "title", True)
            locations = [_text(j.get("location"), "location")]
            addr = _optional_object(_optional_object(j.get("address"), "address").get("postalAddress"), "postalAddress")
            countries.append(_text(addr.get("addressCountry"), "country"))
            for loc in _list(j.get("secondaryLocations", []), "secondaryLocations"):
                loc = _object(loc, "secondary location")
                locations.append(_text(loc.get("location"), "secondary location name"))
                secondary = _optional_object(loc.get("address"), "secondary address")
                countries.append(_text(secondary.get("addressCountry"), "secondary country"))
            description = _text(j.get("descriptionPlain"), "descriptionPlain")
            published = _text(j.get("publishedAt"), "publishedAt")
            if published:
                dates["publishedAt"] = published  # last published, per Ashby documentation
            salary = _text(_optional_object(j.get("compensation"), "compensation").get("compensationTierSummary"), "compensation summary")
            department, team, employment_type = _meta(j.get("department")), _meta(j.get("team")), _meta(j.get("employmentType"))
        if link_id != identifier:
            raise ValueError("posting ID and URL disagree")
        if identifier in identities:
            raise ValueError("duplicate posting identity")
        identities.add(identifier)
        rows.append(Posting(company, kind, board, identifier, title,
                            ", ".join(dict.fromkeys(x for x in locations if x)), link,
                            description, published, salary, countries[0] if countries else "",
                            tuple(dict.fromkeys(x for x in countries if x)), dates, j,
                            department, team, employment_type))
    if stats is not None:
        stats["skipped_non_vacancies"] = skipped
        stats["source_records"] = len(jobs)
    return rows
