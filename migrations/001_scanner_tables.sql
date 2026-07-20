-- AgroMind Price Scanner — tabele Supabase
-- Rulează în Supabase SQL Editor sau prin migration CLI

create table if not exists scanner_products (
  id uuid primary key default gen_random_uuid(),
  shop text not null,
  url text not null unique,
  name text not null,
  brand text,
  sku text,
  gtin text,
  image text,
  product_type text check (product_type in ('erbicid','fungicid','insecticid','ingrasamant','samanta','altul')),
  active_substance text,
  concentration text,
  target_crops text[],
  enriched_at timestamptz,
  first_seen timestamptz default now(),
  last_seen timestamptz default now()
);

create index if not exists idx_scanner_products_shop on scanner_products(shop);
create index if not exists idx_scanner_products_gtin on scanner_products(gtin) where gtin is not null;
create index if not exists idx_scanner_products_active_substance on scanner_products(active_substance) where active_substance is not null;
create index if not exists idx_scanner_products_enriched on scanner_products(enriched_at) where enriched_at is null;

create table if not exists scanner_prices (
  id bigint generated always as identity primary key,
  product_id uuid references scanner_products(id) on delete cascade,
  price numeric(10,2) not null,
  in_stock boolean default true,
  scanned_at timestamptz default now()
);

create index if not exists idx_scanner_prices_product on scanner_prices(product_id, scanned_at desc);

create table if not exists scanner_matches (
  id uuid primary key default gen_random_uuid(),
  method text not null check (method in ('gtin','ai')),
  created_at timestamptz default now()
);

create table if not exists scanner_match_members (
  match_id uuid references scanner_matches(id) on delete cascade,
  product_id uuid references scanner_products(id) on delete cascade,
  primary key (match_id, product_id)
);

-- RLS: service key bypasses, anon nu poate accesa
alter table scanner_products enable row level security;
alter table scanner_prices enable row level security;
alter table scanner_matches enable row level security;
alter table scanner_match_members enable row level security;

-- Allow read pentru aplicația AgroMind (anon key)
create policy "Public read scanner_products" on scanner_products for select using (true);
create policy "Public read scanner_prices" on scanner_prices for select using (true);
create policy "Public read scanner_matches" on scanner_matches for select using (true);
create policy "Public read scanner_match_members" on scanner_match_members for select using (true);
