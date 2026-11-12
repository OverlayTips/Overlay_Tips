-- Overlay Tips members portal: run this once in Supabase → SQL Editor → New query → Run.
-- Then make yourself admin (last line, with your email) after you've signed up on overlay.tips/portal.html.

-- ---------- members ----------
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  name text,
  tier text not null default 'pending' check (tier in ('pending','founder','core','vip')),
  is_admin boolean not null default false,
  ref_code text unique default substr(md5(random()::text), 1, 8),
  referred_by text,
  created_at timestamptz not null default now()
);

-- new sign-up -> profile row (name + referral code come from the sign-up form)
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  active int;
begin
  -- first 500 sign-ups are switched on automatically as Founding members
  select count(*) into active from public.profiles where tier <> 'pending' and not is_admin;
  insert into public.profiles (id, email, name, referred_by, tier)
  values (new.id, new.email,
          nullif(trim(new.raw_user_meta_data->>'name'), ''),
          nullif(trim(new.raw_user_meta_data->>'ref'), ''),
          case when active < 500 then 'founder' else 'pending' end);
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((select is_admin from profiles where id = auth.uid()), false) $$;

create or replace function public.my_tier() returns text
language sql stable security definer set search_path = public as $$
  select case when is_admin then 'vip' else tier end from profiles where id = auth.uid() $$;

create or replace function public.is_member() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(public.my_tier() in ('founder','core','vip'), false) $$;

-- how many mates you've referred (and how many are active members)
create or replace function public.my_referrals() returns table(total int, active int)
language sql stable security definer set search_path = public as $$
  select count(*)::int, count(*) filter (where tier <> 'pending')::int
  from profiles where referred_by = (select ref_code from profiles where id = auth.uid()) $$;

alter table public.profiles enable row level security;
drop policy if exists "read own or admin" on public.profiles;
create policy "read own or admin" on public.profiles for select using (id = auth.uid() or public.is_admin());
drop policy if exists "admin updates" on public.profiles;
create policy "admin updates" on public.profiles for update using (public.is_admin()) with check (public.is_admin());
-- members can change their own name only
drop policy if exists "own name" on public.profiles;
revoke update on public.profiles from authenticated;
grant update (name, tier, is_admin) on public.profiles to authenticated;
create or replace function public.guard_profile() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is not null and not public.is_admin() and (new.tier is distinct from old.tier or new.is_admin is distinct from old.is_admin) then
    raise exception 'only admins can change tiers';
  end if;
  return new;
end $$;
drop trigger if exists guard_profile on public.profiles;
create trigger guard_profile before update on public.profiles for each row execute function public.guard_profile();
create policy "own name" on public.profiles for update using (id = auth.uid()) with check (id = auth.uid());

-- ---------- tips ----------
create table if not exists public.tips (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  tip_date date not null default (now() at time zone 'Australia/Melbourne')::date,
  sport text not null,
  event text,
  selection text not null,
  odds numeric(7,2) not null check (odds > 1),
  units numeric(5,2) not null default 1 check (units > 0),
  bookie text,
  reasoning text,
  vip_only boolean not null default false,
  result text not null default 'pending' check (result in ('pending','won','lost','void'))
);
alter table public.tips enable row level security;
drop policy if exists "members read" on public.tips;
create policy "members read" on public.tips for select
  using (public.is_member() and (not vip_only or public.my_tier() = 'vip'));
drop policy if exists "admin writes" on public.tips;
create policy "admin writes" on public.tips for all using (public.is_admin()) with check (public.is_admin());

-- ---------- settings (WhatsApp link, giveaway info) ----------
create table if not exists public.settings (key text primary key, value text);
alter table public.settings enable row level security;
drop policy if exists "members read settings" on public.settings;
create policy "members read settings" on public.settings for select using (public.is_member());
drop policy if exists "admin writes settings" on public.settings;
create policy "admin writes settings" on public.settings for all using (public.is_admin()) with check (public.is_admin());
insert into public.settings (key, value) values
  ('whatsapp', ''),
  ('giveaway', 'This month''s prize gets announced in the group. Members are in the draw automatically.')
on conflict (key) do nothing;

-- ---------- make yourself admin (after signing up on the portal) ----------
-- update public.profiles set is_admin = true, tier = 'vip' where email = 'YOUR@EMAIL.com';
