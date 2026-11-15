-- Overlay Tips Odds Fantasy (free to play for members). Run once in Supabase -> SQL Editor -> New query -> Run.
-- Safe to run again after changes.
--
-- Rounds (one per NBA day, Melbourne time) are written by the odds workflow (scripts/fantasy.py) with the
-- service key: the player pool + odds multipliers until lock, then the stats as games are played.
-- Members save their 5 picks here; the database itself refuses picks after lock or players not in the pool.
--
-- Scoring (same as the rulebook): base points x odds multiplier, x2 for the Overlay Pick.
--   NBA base: point 1, rebound 1.5, assist 1.5, steal 3, block 3
--   multiplier from the 20+ points price at lock: under $2 = 1x, $2-3.99 = 1.5x, $4-7.99 = 2x, $8+ = 3x

-- ---------- rounds (written only by the workflow / service key) ----------
create table if not exists public.fantasy_rounds (
  id text primary key,                         -- e.g. nba-2026-11-16
  sport text not null check (sport in ('nba','afl')),
  label text,
  round_date date not null,                    -- Melbourne date of the games
  lock_at timestamptz not null,                -- first game's start: picks close
  games jsonb not null default '[]'::jsonb,    -- [{id, home, away, start}]
  pool jsonb not null default '[]'::jsonb,     -- [{k, n, t, ev, o, e, m}] k = player key, o = 20+ pts price, m = multiplier
  results jsonb not null default '{}'::jsonb,  -- {k: {pts, reb, ast, stl, blk, min, b (base points), f (final)}}
  status text not null default 'open' check (status in ('open','locked','final')),
  updated_at timestamptz not null default now()
);
alter table public.fantasy_rounds enable row level security;
drop policy if exists "members read rounds" on public.fantasy_rounds;
create policy "members read rounds" on public.fantasy_rounds for select using (public.is_member());
revoke insert, update, delete on public.fantasy_rounds from anon, authenticated;

-- ---------- entries (one per member per round) ----------
create table if not exists public.fantasy_entries (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  round_id text not null references public.fantasy_rounds(id) on delete cascade,
  picks text[] not null,
  overlay text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, round_id)
);
alter table public.fantasy_entries enable row level security;

create or replace function public.fantasy_open(p_round text) returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((select now() < lock_at from fantasy_rounds where id = p_round), false) $$;

drop policy if exists "own entries read" on public.fantasy_entries;
create policy "own entries read" on public.fantasy_entries for select using (user_id = auth.uid());
drop policy if exists "own entries add" on public.fantasy_entries;
create policy "own entries add" on public.fantasy_entries for insert
  with check (user_id = auth.uid() and public.is_member() and public.fantasy_open(round_id));
drop policy if exists "own entries change" on public.fantasy_entries;
create policy "own entries change" on public.fantasy_entries for update
  using (user_id = auth.uid() and public.fantasy_open(round_id))
  with check (user_id = auth.uid() and public.is_member() and public.fantasy_open(round_id));
drop policy if exists "own entries remove" on public.fantasy_entries;
create policy "own entries remove" on public.fantasy_entries for delete
  using (user_id = auth.uid() and public.fantasy_open(round_id));
grant select, insert, update, delete on public.fantasy_entries to authenticated;

-- 5 different players from this round's pool, Overlay Pick is one of them, and only before lock
create or replace function public.fantasy_check_entry() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  r fantasy_rounds;
  bad int;
begin
  select * into r from fantasy_rounds where id = new.round_id;
  if r.id is null then raise exception 'round not found'; end if;
  if now() >= r.lock_at then raise exception 'picks are locked for this round'; end if;
  if coalesce(array_length(new.picks, 1), 0) <> 5 then raise exception 'pick exactly 5 players'; end if;
  if (select count(distinct x) from unnest(new.picks) x) <> 5 then raise exception 'pick 5 different players'; end if;
  if not (new.overlay = any(new.picks)) then raise exception 'your Overlay Pick must be one of your 5'; end if;
  select count(*) into bad from unnest(new.picks) x
    where not exists (select 1 from jsonb_array_elements(r.pool) p where p->>'k' = x);
  if bad > 0 then raise exception 'a player you picked is no longer in this round'; end if;
  new.updated_at := now();
  if tg_op = 'UPDATE' then new.created_at := old.created_at; new.user_id := old.user_id; end if;
  return new;
end $$;
drop trigger if exists fantasy_check_entry on public.fantasy_entries;
create trigger fantasy_check_entry before insert or update on public.fantasy_entries
  for each row execute function public.fantasy_check_entry();

-- ---------- scores ----------
-- every entry's score: total, best single player, players with stats so far
create or replace function public.fantasy_scores(p_sport text, p_from date, p_to date)
returns table (user_id uuid, round_id text, total numeric, best numeric, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select e.user_id, e.round_id,
         coalesce(sum(s.pts), 0) as total, coalesce(max(s.pts), 0) as best, e.created_at
  from fantasy_entries e
  join fantasy_rounds r on r.id = e.round_id
  cross join lateral unnest(e.picks) as k(key)
  left join lateral (
    select coalesce((r.results -> k.key ->> 'b')::numeric, 0)
           * coalesce((select (p->>'m')::numeric from jsonb_array_elements(r.pool) p where p->>'k' = k.key limit 1), 1)
           * (case when k.key = e.overlay then 2 else 1 end) as pts
  ) s on true
  where r.sport = p_sport and r.round_date between p_from and p_to and now() >= r.lock_at
  group by e.user_id, e.round_id, e.created_at $$;
revoke execute on function public.fantasy_scores(text, date, date) from anon, authenticated, public;

-- ladder for a date range (one round = same date twice). Names show as "First L." for privacy.
create or replace function public.fantasy_board(p_sport text, p_from date, p_to date)
returns table (pos int, name text, total numeric, rounds int, best numeric, is_me boolean)
language sql stable security definer set search_path = public as $$
  with t as (
    select s.user_id, sum(s.total) as total, count(*)::int as rounds, max(s.best) as best, min(s.created_at) as first_in
    from fantasy_scores(p_sport, p_from, p_to) s group by s.user_id
  ), ranked as (
    select (row_number() over (order by t.total desc, t.best desc, t.first_in))::int as pos,
           coalesce(
             nullif(trim(split_part(trim(pr.name), ' ', 1)
               || case when position(' ' in trim(pr.name)) > 0
                       then ' ' || upper(left(trim(substr(trim(pr.name), position(' ' in trim(pr.name)) + 1)), 1)) || '.'
                       else '' end), ''),
             'Member ' || upper(left(coalesce(pr.ref_code, 'xxxx'), 4))) as name,
           round(t.total, 1) as total, t.rounds, round(t.best, 1) as best, t.user_id = auth.uid() as is_me
    from t left join profiles pr on pr.id = t.user_id
  )
  select * from ranked
  where public.is_member() and (ranked.pos <= 100 or ranked.is_me)   -- top 100, plus you wherever you are
  order by ranked.pos $$;
grant execute on function public.fantasy_board(text, date, date) to authenticated;

-- how many members have entered a round (shown before lock)
create or replace function public.fantasy_count(p_round text) returns int
language sql stable security definer set search_path = public as $$
  select case when public.is_member() then (select count(*)::int from fantasy_entries where round_id = p_round) else 0 end $$;
grant execute on function public.fantasy_count(text) to authenticated;
