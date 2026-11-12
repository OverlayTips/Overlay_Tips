-- Overlay Tips: paid memberships (Stripe). Run once in Supabase -> SQL Editor -> New query -> Run.
-- The billing worker (cloudflare/billing-worker.js) fills these in when someone pays, renews or cancels.
-- Members can read their own row but can't change any of these columns.

alter table public.profiles
  add column if not exists stripe_customer_id text unique,
  add column if not exists stripe_sub_id text,
  add column if not exists sub_status text,          -- active, trialing, past_due, canceled ...
  add column if not exists sub_plan text,            -- core-month, core-week, vip-month, vip-week
  add column if not exists paid_until timestamptz,   -- next renewal (or when access ends after cancelling)
  add column if not exists base_tier text,           -- tier to drop back to when a paid plan ends (founder or pending)
  add column if not exists free_weeks int not null default 0,              -- referral weeks earned while not paying
  add column if not exists referral_rewarded boolean not null default false; -- their referrer already got the free week
