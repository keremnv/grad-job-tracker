-- Symbol-preserving PostgreSQL-native FTS (C / C++ / C# / F# / .NET) and
-- operational join indexes to eliminate O(N^2) sequential scans on url_exact
-- and current_version_id as the monitored corpus scales.
SET LOCAL search_path = jobwatch, pg_catalog;

CREATE FUNCTION symbol_search_text(input text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
  SELECT regexp_replace(
    regexp_replace(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            input,
            '(^|[^A-Za-z0-9_])c\+\+(?![A-Za-z_+])', '\1sym_cpp ', 'gi'
          ),
          '(^|[^A-Za-z0-9_])c#(?![A-Za-z0-9_#])', '\1sym_csharp ', 'gi'
        ),
        '(^|[^A-Za-z0-9_])f#(?![A-Za-z0-9_#])', '\1sym_fsharp ', 'gi'
      ),
      '(^|[^A-Za-z0-9_])asp\.net(?![A-Za-z0-9_])', '\1asp sym_dotnet ', 'gi'
    ),
    '(^|[^A-Za-z0-9_])\.net(?![A-Za-z0-9_])', '\1sym_dotnet ', 'gi'
  );
$$;

GRANT EXECUTE ON FUNCTION symbol_search_text(text) TO jobwatch_collector, jobwatch_owner;

ALTER TABLE posting_versions
  ADD COLUMN search_sym_tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple'::regconfig, jobwatch.symbol_search_text(title)), 'A') ||
    setweight(to_tsvector('simple'::regconfig, jobwatch.symbol_search_text(company || ' ' || department || ' ' || team || ' ' || location_text)), 'B') ||
    setweight(to_tsvector('simple'::regconfig, jobwatch.symbol_search_text(coalesce(description_text, ''))), 'D')
  ) STORED;

CREATE INDEX versions_search_sym ON posting_versions USING gin (search_sym_tsv);
CREATE INDEX versions_url_exact ON posting_versions (url_exact);
CREATE INDEX postings_current_version_idx ON postings (current_version_id);

COMMENT ON COLUMN posting_versions.search_sym_tsv IS 'Symbol-preserving simple FTS vector keeping C, C++, C#, F#, and .NET distinct for PostgreSQL-native lexical search and GIN-prefiltered BM25.';
