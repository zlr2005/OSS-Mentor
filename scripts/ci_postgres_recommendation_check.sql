-- CI assertion for the PostgreSQL recommendation snapshot migration.
-- Run after PostgreSQL migrations 001, 002 and 003.

DO $$
DECLARE
    missing_tables TEXT;
    guide_type TEXT;
    rerank_type TEXT;
BEGIN
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
    INTO missing_tables
    FROM (
        VALUES ('recommendation_run'), ('recommendation_run_item')
    ) AS expected(table_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = expected.table_name
    );

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'missing PostgreSQL recommendation tables: %', missing_tables;
    END IF;

    SELECT data_type
    INTO guide_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'repository'
      AND column_name = 'has_contributing_guide';

    IF guide_type IS DISTINCT FROM 'boolean' THEN
        RAISE EXCEPTION 'repository.has_contributing_guide must be BOOLEAN, got %', guide_type;
    END IF;

    SELECT data_type
    INTO rerank_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'recommendation_run_item'
      AND column_name = 'diversity_reranked';

    IF rerank_type IS DISTINCT FROM 'boolean' THEN
        RAISE EXCEPTION 'recommendation_run_item.diversity_reranked must be BOOLEAN, got %', rerank_type;
    END IF;
END;
$$;
