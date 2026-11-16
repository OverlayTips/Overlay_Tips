-- Overlay Tips Discord roles: run once in Supabase -> SQL Editor -> New query -> Run.
-- A member links their Discord in the portal (overlay.tips/discord.html hands back a short-lived Discord token).
-- scripts/discord_roles.py (every odds update) checks the token with Discord, adds them to the server, records
-- their Discord id, throws the token away and keeps their role (VIP / Daily Card / Founder) in step with their tier.
-- Members can only write the token: the Discord id is only ever set by the server, so nobody can claim someone
-- else's Discord account.
create table if not exists public.discord_links (
  user_id uuid primary key default auth.uid() references public.profiles(id) on delete cascade,
  access_token text,
  discord_id text,
  discord_name text,
  synced_tier text,
  error text,
  linked_at timestamptz not null default now()
);
alter table public.discord_links enable row level security;
drop policy if exists "own discord link" on public.discord_links;
create policy "own discord link" on public.discord_links for all using (user_id = auth.uid()) with check (user_id = auth.uid());
revoke all on public.discord_links from anon, authenticated;
grant select, delete on public.discord_links to authenticated;
grant insert (user_id, access_token), update (user_id, access_token) on public.discord_links to authenticated;
