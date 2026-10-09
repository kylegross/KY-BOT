CREATE TABLE IF NOT EXISTS boards (
                channel BIGINT PRIMARY KEY, guild BIGINT NOT NULL, log_channel BIGINT NOT NULL,
                message BIGINT, page BIGINT NOT NULL DEFAULT 0, dirty BIGINT NOT NULL DEFAULT 1, name TEXT NOT NULL DEFAULT 'ADMIN CHECKLIST', style TEXT NOT NULL DEFAULT 'gold', height BIGINT NOT NULL DEFAULT 280, footer_text TEXT, role_id BIGINT, header_background BYTEA, footer_background BYTEA, footer_icon BYTEA, task_view TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS tasks (
                id BIGSERIAL PRIMARY KEY, channel BIGINT NOT NULL,
                source BIGINT NOT NULL UNIQUE, author BIGINT NOT NULL, title TEXT NOT NULL,
                attachments TEXT NOT NULL, done BIGINT NOT NULL DEFAULT 0,
                revision BIGINT NOT NULL DEFAULT 0, thread BIGINT,
                seeded BIGINT NOT NULL DEFAULT 0, source_deleted BIGINT NOT NULL DEFAULT 0, category_id BIGINT, awaiting_category BIGINT NOT NULL DEFAULT 0, category_prompt BIGINT, deleted BIGINT NOT NULL DEFAULT 0, deleted_by BIGINT, priority TEXT NOT NULL DEFAULT 'medium', position BIGINT NOT NULL DEFAULT 0, source_title TEXT, thread_sync BIGINT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS logs (
                id BIGSERIAL PRIMARY KEY, channel BIGINT NOT NULL,
                task BIGINT NOT NULL, title TEXT NOT NULL, actor BIGINT NOT NULL,
                created DOUBLE PRECISION NOT NULL, sent BIGINT NOT NULL DEFAULT 0, log_channel BIGINT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS categories (id BIGSERIAL PRIMARY KEY, channel BIGINT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL, position BIGINT NOT NULL DEFAULT 0, revision BIGINT NOT NULL DEFAULT 0, UNIQUE(channel,name_key));
CREATE TABLE IF NOT EXISTS guild_settings (guild_id TEXT PRIMARY KEY, log_channel_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS welcome_settings (guild_id TEXT PRIMARY KEY, channel_id TEXT, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS welcome_artwork (guild_id TEXT PRIMARY KEY, background BYTEA, title TEXT NOT NULL DEFAULT 'WELCOME', accent TEXT NOT NULL DEFAULT 'C9DDF0', dim BIGINT NOT NULL DEFAULT 35);
CREATE INDEX IF NOT EXISTS tasks_channel_idx ON tasks(channel);
CREATE INDEX IF NOT EXISTS tasks_thread_idx ON tasks(thread);
CREATE INDEX IF NOT EXISTS boards_guild_idx ON boards(guild);

ALTER TABLE boards ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS subtasks (
    id BIGSERIAL PRIMARY KEY, task BIGINT NOT NULL, title TEXT NOT NULL,
    done BIGINT NOT NULL DEFAULT 0, deleted BIGINT NOT NULL DEFAULT 0,
    revision BIGINT NOT NULL DEFAULT 0, position BIGINT NOT NULL DEFAULT 0, author BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS subtasks_task_idx ON subtasks(task);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS discussion_message BIGINT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS discussion_dirty BIGINT NOT NULL DEFAULT 1;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS discussion_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS subtask_page BIGINT NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS thread_activity DOUBLE PRECISION;
