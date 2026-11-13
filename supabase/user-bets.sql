-- Personal accounts: each member's bet tracker saved to their own account (any device), and their own
-- Results & stats in the portal. Run once in Supabase → SQL Editor → New query → Run.
create table if not exists public.user_bets (
  user_id uuid primary key references auth.users(id) on delete cascade,
  bets jsonb not null default '[]'::jsonb,       -- the tracker's bets, as saved on the device
  del jsonb not null default '[]'::jsonb,        -- [{id, t}] bets deleted on some device, so they stay deleted everywhere
  updated_at timestamptz not null default now()
);
alter table public.user_bets enable row level security;
drop policy if exists "own bets" on public.user_bets;
create policy "own bets" on public.user_bets for all using (user_id = auth.uid()) with check (user_id = auth.uid());
