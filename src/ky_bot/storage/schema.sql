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
CREATE TABLE IF NOT EXISTS member_log_settings (guild_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS command_center_panels (guild_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, message_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS member_inviters (guild_id TEXT NOT NULL, member_id TEXT NOT NULL, inviter_id TEXT, PRIMARY KEY(guild_id, member_id));
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

CREATE TABLE IF NOT EXISTS autothread_rules (id BIGINT PRIMARY KEY, guild BIGINT NOT NULL, channel BIGINT NOT NULL, content TEXT NOT NULL, enabled BIGINT NOT NULL DEFAULT 0, require_attachment BIGINT NOT NULL DEFAULT 0, media_only BIGINT NOT NULL DEFAULT 0, ignore_webhooks BIGINT NOT NULL DEFAULT 1, archive BIGINT NOT NULL DEFAULT 1440, starts_at DOUBLE PRECISION NOT NULL DEFAULT 0, starter_message TEXT, embed_color BIGINT);
CREATE TABLE IF NOT EXISTS autothread_processed (message BIGINT PRIMARY KEY, guild BIGINT NOT NULL, channel BIGINT NOT NULL, rule_id BIGINT NOT NULL, done BIGINT NOT NULL DEFAULT 0, attempts BIGINT NOT NULL DEFAULT 0, retry_at DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS autothread_welcome_deliveries(message BIGINT PRIMARY KEY,description TEXT,color BIGINT);
CREATE TABLE IF NOT EXISTS autothread_notices (message BIGINT PRIMARY KEY, channel BIGINT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS autothread_retired_rules (id BIGINT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS autoreact_rules (
    guild BIGINT NOT NULL, channel BIGINT NOT NULL, settings TEXT NOT NULL,
    enabled INTEGER NOT NULL, revision TEXT NOT NULL, starts DOUBLE PRECISION NOT NULL,
    error TEXT NOT NULL DEFAULT '', PRIMARY KEY(guild,channel)
);
CREATE TABLE IF NOT EXISTS autoreact_jobs (
    message BIGINT PRIMARY KEY, guild BIGINT, channel BIGINT, parent BIGINT,
    revision TEXT, emojis TEXT, position INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0, retry DOUBLE PRECISION DEFAULT 0, created DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS embed_designs (
    guild_id TEXT PRIMARY KEY, revision TEXT NOT NULL, profiles TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leveling_settings (
    guild BIGINT PRIMARY KEY, revision TEXT NOT NULL, settings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leveling_members (
    guild BIGINT NOT NULL, member BIGINT NOT NULL, xp BIGINT NOT NULL DEFAULT 0,
    message_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    voice_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    source BIGINT NOT NULL DEFAULT 0, fingerprint TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(guild,member)
);
CREATE INDEX IF NOT EXISTS leveling_rank_idx ON leveling_members(guild,xp DESC,member);
CREATE TABLE IF NOT EXISTS leveling_adjustments (
    guild BIGINT NOT NULL, operation BIGINT NOT NULL, actor BIGINT NOT NULL,
    member BIGINT NOT NULL, action TEXT NOT NULL, value BIGINT NOT NULL,
    before_xp BIGINT NOT NULL, after_xp BIGINT NOT NULL,
    created DOUBLE PRECISION NOT NULL, PRIMARY KEY(guild,operation)
);

-- Ask to DM/FR consent workflow.
CREATE TABLE IF NOT EXISTS ask_settings (guild BIGINT PRIMARY KEY, revision TEXT NOT NULL, settings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ask_requests (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, sender BIGINT NOT NULL, recipient BIGINT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, data TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ask_active_pair ON ask_requests(guild,sender,recipient,kind) WHERE state IN ('pending','accepted');
CREATE TABLE IF NOT EXISTS ask_drafts (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, sender BIGINT NOT NULL, created DOUBLE PRECISION NOT NULL, request TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ask_events (id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, done BIGINT NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ask_event_queue ON ask_events(done,created);

-- Saved custom embeds and sticky delivery.
CREATE TABLE IF NOT EXISTS custom_embeds (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, revision TEXT NOT NULL, draft TEXT NOT NULL, published TEXT NOT NULL DEFAULT '', channel BIGINT NOT NULL DEFAULT 0, message BIGINT NOT NULL DEFAULT 0, message_count BIGINT NOT NULL DEFAULT 0, last_seen BIGINT NOT NULL DEFAULT 0, sticky_every BIGINT NOT NULL DEFAULT 0, pending BIGINT NOT NULL DEFAULT 0, retired TEXT NOT NULL DEFAULT '[]', deleted BIGINT NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS custom_embed_channel ON custom_embeds(guild,channel,sticky_every);

CREATE TABLE IF NOT EXISTS steal_destinations (user_id BIGINT PRIMARY KEY, guild_id BIGINT NOT NULL);

CREATE TABLE IF NOT EXISTS server_log_exclusions (guild_id TEXT PRIMARY KEY,channels TEXT NOT NULL);

-- Multi-panel ticketing
CREATE TABLE IF NOT EXISTS ticket_settings (guild BIGINT PRIMARY KEY, revision TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, owner BIGINT NOT NULL, channel BIGINT NOT NULL DEFAULT 0, state TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tickets_guild_owner ON tickets(guild,owner,state);
CREATE INDEX IF NOT EXISTS tickets_channel ON tickets(guild,channel);
CREATE TABLE IF NOT EXISTS ticket_events (id TEXT PRIMARY KEY, ticket TEXT NOT NULL, destination BIGINT NOT NULL, delivered BIGINT NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL, data TEXT NOT NULL, retry_at DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ticket_events_pending ON ticket_events(delivered,created);
CREATE TABLE IF NOT EXISTS ticket_archives (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, created DOUBLE PRECISION NOT NULL, body TEXT NOT NULL);
