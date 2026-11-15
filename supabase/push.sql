-- Overlay Tips app alerts (web push): run once in Supabase -> SQL Editor -> New query -> Run.
-- One row per device a member turns alerts on for. The member picks which alerts they want (prefs);
-- what they're allowed is set by their tier when alerts are sent (scripts/web_push.py).
create table if not exists public.push_subs (
  endpoint text primary key,
  user_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  p256dh text not null,
  auth text not null,
  prefs jsonb not null default '{"botd":true,"nba":true,"nfl":true,"nbl":true,"racing":true,"news":true,"games":true,"arbs":true}'::jsonb,
  ua text,
  created_at timestamptz not null default now(),
  last_ok timestamptz
);
alter table public.push_subs enable row level security;
drop policy if exists "own devices" on public.push_subs;
create policy "own devices" on public.push_subs for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create index if not exists push_subs_user on public.push_subs (user_id);
