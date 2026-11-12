-- Overlay Tips: let people join the members portal straight away (no DM needed).
-- Run once in Supabase -> SQL Editor -> New query -> Run.
-- New sign-ups become Founding members automatically while there are fewer than 500 active members.
-- After that they sign up as "Not active" and you switch them on from the admin page.

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  active int;
begin
  select count(*) into active from public.profiles where tier <> 'pending' and not is_admin;
  insert into public.profiles (id, email, name, referred_by, tier)
  values (new.id, new.email,
          nullif(trim(new.raw_user_meta_data->>'name'), ''),
          nullif(trim(new.raw_user_meta_data->>'ref'), ''),
          case when active < 500 then 'founder' else 'pending' end);
  return new;
end $$;

-- anyone who already signed up and is still waiting gets switched on too
update public.profiles set tier = 'founder' where tier = 'pending' and not is_admin;
