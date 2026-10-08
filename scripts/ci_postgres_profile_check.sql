-- CI assertion for the PostgreSQL profile migration.
-- Run after db/postgres/001_initial.sql and db/postgres/002_profile_identity.sql.

DO $$
DECLARE
    missing_tables TEXT;
    binding_user_type TEXT;
BEGIN
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('profile_user_binding'),
            ('profile_field_state'),
            ('github_profile_import'),
            ('profile_field_suggestion'),
            ('developer_skill_evidence')
    ) AS expected(table_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = expected.table_name
    );

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'missing PostgreSQL profile tables: %', missing_tables;
    END IF;

    SELECT data_type
    INTO binding_user_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'profile_user_binding'
      AND column_name = 'user_id';

    IF binding_user_type IS DISTINCT FROM 'bigint' THEN
        RAISE EXCEPTION 'profile_user_binding.user_id must be BIGINT, got %', binding_user_type;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'profile_identity_delete'
          AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'missing profile_identity_delete trigger';
    END IF;
END;
$$;
