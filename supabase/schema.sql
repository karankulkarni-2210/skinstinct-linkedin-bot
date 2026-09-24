-- Schema for the Skinstinct bot (applied to Supabase project "skinstinct-linkedin-bot").
-- After applying, set the secret (same value as the DB_SECRET env var in Vercel):
--   insert into private.bot_config (id, secret) values (1, '<DB_SECRET>')
--   on conflict (id) do update set secret = excluded.secret;

create schema if not exists private;
create table private.bot_config (id int primary key default 1 check (id = 1), secret text not null);

create or replace function private.bot_ok() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from private.bot_config
    where secret = coalesce((current_setting('request.headers', true)::json ->> 'x-bot-secret'), '')
  );
$$;
revoke all on function private.bot_ok() from public;
grant usage on schema private to anon;
grant execute on function private.bot_ok() to anon;

create table public.notes (
  id bigint generated always as identity primary key,
  source text,
  tg_message_id bigint,
  text text not null,
  text_hash text generated always as (md5(text)) stored unique,
  created_at timestamptz not null default now(),
  status text not null default 'new',
  score int,
  pillar text,
  triage jsonb
);
create table public.drafts (
  id bigint generated always as identity primary key,
  note_id bigint not null references public.notes(id) on delete cascade,
  version int not null default 1,
  body text not null,
  meta jsonb,
  status text not null default 'pending',
  created_at timestamptz not null default now()
);
create index drafts_note_id_idx on public.drafts(note_id);
create index notes_status_score_idx on public.notes(status, score desc);
create table public.kv (k text primary key, v text);
create table public.processed_updates (update_id bigint primary key, created_at timestamptz not null default now());

alter table public.notes enable row level security;
alter table public.drafts enable row level security;
alter table public.kv enable row level security;
alter table public.processed_updates enable row level security;

create policy bot_all on public.notes for all to anon using (private.bot_ok()) with check (private.bot_ok());
create policy bot_all on public.drafts for all to anon using (private.bot_ok()) with check (private.bot_ok());
create policy bot_all on public.kv for all to anon using (private.bot_ok()) with check (private.bot_ok());
create policy bot_all on public.processed_updates for all to anon using (private.bot_ok()) with check (private.bot_ok());

revoke all on public.notes, public.drafts, public.kv, public.processed_updates from authenticated;
grant select, insert, update, delete on public.notes, public.drafts, public.kv, public.processed_updates to anon;
