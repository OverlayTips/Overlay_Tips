-- Overlay Tips: unlock live tracking (Daily card + VIP) and the odds tracker (VIP) from the member's login.
-- Put your two passwords in the last lines, then run once in Supabase -> SQL Editor -> New query -> Run.
-- Change a password later: edit the value and run just the insert again.

create table if not exists public.member_keys (
  name text primary key,
  need text not null check (need in ('founder','core','vip')),
  value text not null
);
alter table public.member_keys enable row level security;
drop policy if exists "tier reads keys" on public.member_keys;
create policy "tier reads keys" on public.member_keys for select using (
  (case public.my_tier() when 'vip' then 3 when 'core' then 2 when 'founder' then 1 else 0 end)
  >= (case need when 'vip' then 3 when 'core' then 2 else 1 end)
);
revoke all on public.member_keys from anon;
grant select on public.member_keys to authenticated;

insert into public.member_keys (name, need, value) values
  ('live', 'core', 'TYPE-YOUR-LIVE-PASSWORD-HERE'),
  ('odds', 'vip',  'TYPE-YOUR-ODDS-PASSWORD-HERE')
on conflict (name) do update set need = excluded.need, value = excluded.value;
