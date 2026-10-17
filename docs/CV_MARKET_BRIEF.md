# CV market brief — UK 2027 graduate applications

Evidence as of **17 October 2026 (UTC)**. Prepared for deciding how many CV variants
to maintain. **This is a selected sample of 15 job descriptions, not a statistically
representative account of the UK graduate market.** It does not draft or change any CV.

Sources: [`data/role_sample.json`](../data/role_sample.json) (the 15 descriptions,
classified), [`reports/leads.md`](../reports/leads.md) (dated leads and programme
cycles from `data/leads.json`), [`reports/requirements.md`](../reports/requirements.md)
(automatic mention counts for 11 feed-collected descriptions).

## 1. What was verified, and how

- **15 descriptions from 12 employers** in three role families: 5 general
  software/backend, 6 infrastructure/systems, 4 AI/ML. Smaller firms and startups:
  incident.io, hyperexponential, Riverlane, Arctic Lake, Fifty One Degrees, Resonance.
- Each was read from the employer's own careers page or from a public ATS posting
  API (Greenhouse, Lever, Ashby) for a board linked from the employer's site.
  Arctic Lake's record was read from the published data in its site's JavaScript
  bundle; the rendered page was not inspected.
- **3 of 15 are not verified as employer-owned sources**: DV Trading (employer site
  returned HTTP 526), Fifty One Degrees and Resonance (Ashby boards self-identify,
  but no employer website was confirmed).
- No application form was opened, filled or submitted. No browser automation,
  LinkedIn or login was used. Revolut's 2027 Applied AI graduate page returned an
  HTTP 403 bot check and was not bypassed.

## 2. Time-sensitive shortlist

Days are counted from 17 October 2026. No source states an expiry time or timezone;
none is assumed.

| When | Employer — role | Status (source wording) |
|---|---|---|
| **29 Oct 2026 (12 days)** | incident.io — Product Engineer Graduate, London | "open until 29 October 2026" (programme-wide); start June or September 2027 |
| **6 Nov 2026 (20 days)** | Riverlane — Engineering Graduate Scheme 2027, Cambridge | "submit your application by Friday 6 November 2026"; CV **and covering letter**; challenges by 7 Dec; assessment day 14 Jan 2027 |
| Rolling — apply early | Cambridge Consultants — Graduate Software / Cloud Software / AI / Human-Centric AI Engineer (2027 start) | "no deadline … rolling basis. Once the position is filled, we will close the advert"; join May–Oct 2027 |
| No deadline stated | Softwire — Graduate Consultant Software Engineer 2027 | "Applications are now open"; intakes Sep/Nov 2027 |
| No deadline stated | Marshall Wace — Software/Infrastructure Graduate 2027, London | Start 6 Sep 2027; apply to **one** role only |
| No deadline stated | G-Research — Graduate Machine Learning Engineer, London | Start 31 Aug 2027 |
| No deadline stated | Arctic Lake — Graduate Software Engineer (High Performance Systems) | Start 2027; Java first-round challenge; on-site |
| No deadline stated | DV Trading — Graduate Software Engineer (DV Commodities) | Graduate by Summer 2027; on-site 5 days; board ownership unverified |
| Announced window | JLR — Engineering Graduate (Software pathway) | Overview: "Applications: January 2027", start Sep 2027; pathway page says dates "TBC" |
| Register interest only | Ocado Group — graduate programmes | "be the first to know when applications officially open this winter"; year not stated |
| Closed, reopening unknown | Scott Logic; BT Group (Software Engineering, Data and AI) | No reopening date stated (Scott Logic's Lever board is now collected) |
| No 2027 programme found | hyperexponential | Its 2026 programme ("Start date: September 2026") is **not** evidence of a 2027 intake; board now collected |
| Nothing current | Tracebit, Bloxd, Relay Technologies, Healf | No graduate/early-career engineering posting on official or identified boards |

Unclear-intake but listed: Palantir Software Engineer New Grad and New Grad –
Infrastructure (no graduation year or start date stated), Fifty One Degrees Graduate AI
Engineer (no start date; requires UK right to work at application), Resonance Graduate
AI Role (Summer 2027; employer site unverified), hyperexponential Associate Delivery
Engineer (asks for production-grade Python; likely a stretch).

## 3. Recurring requirements by role family

Classification: **mandatory** = framed as required/need/must or an eligibility
condition; **preferred** = framed as desirable/bonus/nice-to-have/not essential;
**unclear** = listed without stated strictness (most "What you can bring" lists).
Three postings (A1, A2, A4) state no mandatory criteria at all.

**Across all families (15)**
- Python appears in the stated criteria of **11/15**; C++ in 5/15; Java in 3/15;
  TypeScript anywhere in 6/15 (mostly stack context, not a requirement).
- Most entry criteria are language-agnostic ("any programming language", "at least
  one of", "or similar"). Only three postings fix a language: hyperexponential
  (Python, production-grade), DV Trading (C++ and/or Python on Linux), Arctic Lake
  (Java first-round challenge).
- **Evidence through projects** is requested explicitly in 6/15 (personal, academic,
  hackathon or placement projects; Resonance asks for GitHub and something built with AI).
- **AI-assisted development appears outside AI roles**: DV Trading makes using AI tools
  a stated requirement; Softwire trains it; incident.io and Cambridge Consultants
  Cloud describe AI agent work.
- Application artefacts vary: A Level subjects and grades on the CV (all Cambridge
  Consultants roles), covering letter (Riverlane), written answers (Palantir), online
  tests and assessment days (Softwire, Riverlane). Softwire states no grade requirements.

**Software / backend (S1–S5)** — CS, engineering or STEM background (Softwire also
accepts proficient coders without a degree); one mainstream language; data structures
and software-engineering fundamentals; communication and ownership. Stacks mentioned
as context only: Java/Go/TypeScript (Palantir), Go/TypeScript/React/Postgres/GCP
(incident.io).

**Infrastructure / systems (I1–I6)** — the same core plus emphasis on C++ or Python
(C++ is named in 3 of 6), Linux (DV Trading), cloud familiarity (Palantir, Cambridge
Consultants Cloud), low-latency trading systems (Arctic Lake, DV Trading) and
rotations across software, data and infrastructure (Marshall Wace). Two state
eligibility that includes June 2027 graduation (DV Trading, Riverlane).
Marshall Wace's posting lists no requirements; its programme page says a CS degree
"is not a requirement" but wants skills shown "through internships, hackathons, or
personal projects".

**AI / ML (A1–A4)** — Python in all four. Two distinct sub-profiles:
- *Research-oriented ML* (G-Research): ML or related degree; PyTorch/NumPy
  "desirable"; optimisation, HPC, profiling, implementing papers.
- *Applied / LLM engineering* (Cambridge Consultants AI, Fifty One Degrees,
  Resonance): building agents, data pipelines and integrations; LLM/VLM and agentic
  workflows "not essential" but valued; client-facing work in two consultancies.

The automatic counts in `reports/requirements.md` (11 feed descriptions, grouped by
title heuristics) are consistent: Python is mentioned in all 4 software, both ML/AI
and 2 of 3 platform descriptions (the one backend description, Monzo's experienced
Backend Engineer III, does not mention it); C++ and Go appear in software and platform
groups; PyTorch appears only in ML/AI, and LLMs in ML/AI plus one platform post.

## 4. Gaps in the evidence

- **Small, selected sample.** Employers were chosen from the owner's list, the named
  leads and accessible boards. London/Cambridge dominate; nothing from Sheffield or
  Leeds; Manchester only via Softwire.
- **AI/ML is thin**: 4 descriptions, 2 from employers whose websites were not
  verified. No MLOps, research-software-engineer or large-tech ML-platform description.
- **Large structured schemes are under-represented**: JLR, BT, Scott Logic and Ocado
  are closed or register-interest only, so their 2027 requirements are unknown.
  Bloomberg and Revolut pages could not be read.
- Palantir's new-grad postings state no year and may be long-running requisitions.
- Owner eligibility is unknown: UK right to work (required at application by Fifty
  One Degrees), sponsorship (stated as available by Softwire, Arctic Lake, Resonance;
  G-Research covers immigration costs), on-site requirements, and A Level grades.
- Mandatory/preferred depends on wording; many criteria are "unclear" by construction.
- Application forms were not inspected, so required questions or tests are unknown.

## 5. Provisional recommendation on CV variants

**Two variants are justified by this sample; a third is not yet.**

1. **Software / infrastructure CV** for both general software and systems roles. The
   two families share the same core criteria; differences are emphasis (C++, Linux,
   cloud, low-latency for infrastructure; shipping product features for product roles)
   and can be handled by reordering projects and skills within one document.
2. **AI / ML engineering CV** leading with Python ML work, model training/evaluation,
   and built LLM/agent projects. AI/ML postings ask for different evidence (ML
   frameworks, built-with-AI projects, research reading) that would crowd a general CV.

Revisit if more than ~3 low-latency/trading applications are planned (a systems-first
variant might then pay off), or if applications concentrate on research-oriented ML
(G-Research style) rather than applied/LLM roles, which emphasise different evidence.
Employer-specific items (A Level grades for Cambridge Consultants, covering letter for
Riverlane, GitHub and an AI-built note for Resonance) are per-application additions,
not extra variants.

Confidence: **provisional** — 15 selected descriptions, three not verified as
employer-owned sources.

## 6. Collection changes made in this pass (now stopped)

Six boards were enabled after verifying each mapping from the employer's own site:
Cambridge Consultants and Riverlane (EU-hosted Greenhouse, new `region: "eu"` support),
Marshall Wace (Greenhouse), Scott Logic (Lever), hyperexponential and Tracebit (Ashby).
DV Trading, Fifty One Degrees, Resonance and Bloxd remain manual because ownership
could not be verified. Per the stop condition, no further infrastructure was added:
no dashboard, notifications, LinkedIn collection or applications.
