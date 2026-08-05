create table if not exists public.orders (
  id uuid primary key,
  idempotency_key text not null unique,
  payload_hash text not null check (length(payload_hash) = 64),
  items jsonb not null,
  subtotal_cents integer not null check (subtotal_cents >= 0),
  tax_cents integer not null check (tax_cents >= 0),
  total_cents integer not null check (total_cents >= 0),
  created_at timestamptz not null default now()
);

-- Migration for an existing development table created before payload hashes:
-- alter table public.orders add column if not exists payload_hash text;
-- update public.orders set payload_hash = repeat('0', 64) where payload_hash is null;
-- alter table public.orders alter column payload_hash set not null;
-- alter table public.orders add constraint orders_payload_hash_len check (length(payload_hash) = 64);

alter table public.orders enable row level security;
-- No anon/authenticated policies are created. The backend uses only the server-side
-- Supabase service role key, which must remain server-only and bypasses RLS.
