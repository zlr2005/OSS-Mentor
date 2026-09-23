-- B's additive follow-up to 009. Keep 010 reserved for recommendations.
-- Legacy user_key values are opaque; never infer ownership from their text.
ALTER TABLE profile_user_binding ADD COLUMN user_id INTEGER
    REFERENCES oss_user(user_id) ON DELETE CASCADE;

CREATE UNIQUE INDEX profile_user_binding_identity_idx
    ON profile_user_binding(user_id);

-- Deleting an authenticated account removes its owned profile and, through
-- 009's foreign keys, the profile's import, provenance and evidence records.
-- Soft deletion is handled separately by the platform account lifecycle.
CREATE TRIGGER profile_identity_delete
BEFORE DELETE ON oss_user
BEGIN
    DELETE FROM developer_profile
    WHERE developer_profile_id IN (
        SELECT developer_profile_id FROM profile_user_binding
        WHERE user_id = OLD.user_id
    );
END;
