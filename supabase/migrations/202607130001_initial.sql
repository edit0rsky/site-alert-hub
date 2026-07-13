create extension if not exists pgcrypto;

create type public.delivery_status as enum (
  'pending',
  'sending',
  'sent',
  'failed',
  'retry_requested',
  'dead_letter',
  'skipped_initial_sync'
);

create table public.admin_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

create table public.source_pages (
  id text primary key,
  site_name text not null,
  page_name text not null,
  category text not null,
  list_url text not null,
  base_url text not null,
  parser_key text not null,
  bot_profile text not null,
  enabled boolean not null default true,
  config jsonb not null default '{}'::jsonb,
  initialized boolean not null default false,
  initialized_at timestamptz,
  baseline_article_count integer not null default 0,
  last_crawled_at timestamptz,
  last_success_at timestamptz,
  last_error_at timestamptz,
  last_error_code text,
  last_error_message text,
  consecutive_failure_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.articles (
  id uuid primary key default gen_random_uuid(),
  source_page_id text not null references public.source_pages(id) on delete restrict,
  external_id text,
  title text not null,
  original_url text not null,
  normalized_url text not null,
  article_key text not null,
  published_at text,
  first_detected_at timestamptz not null default now(),
  last_detected_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_page_id, external_id),
  unique (source_page_id, normalized_url),
  unique (source_page_id, article_key)
);

create table public.deliveries (
  id uuid primary key default gen_random_uuid(),
  article_id uuid not null references public.articles(id) on delete restrict,
  bot_profile text not null,
  target_chat_id text not null,
  status public.delivery_status not null default 'pending',
  delivery_generation integer not null default 1 check (delivery_generation > 0),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  telegram_message_id text,
  http_status integer,
  last_error_code text,
  last_error_message text,
  next_retry_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (article_id, bot_profile, target_chat_id, delivery_generation)
);

create table public.delivery_attempts (
  id uuid primary key default gen_random_uuid(),
  delivery_id uuid not null references public.deliveries(id) on delete restrict,
  attempt_number integer not null check (attempt_number > 0),
  status text not null check (status in ('sent', 'failed')),
  http_status integer,
  error_code text,
  error_message text,
  response_time_ms integer,
  attempted_at timestamptz not null default now(),
  unique (delivery_id, attempt_number)
);

create table public.crawl_runs (
  id uuid primary key default gen_random_uuid(),
  github_run_id text,
  github_run_attempt integer,
  trigger_type text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
  source_page_count integer not null default 0,
  success_page_count integer not null default 0,
  failed_page_count integer not null default 0,
  discovered_count integer not null default 0,
  new_article_count integer not null default 0,
  sent_count integer not null default 0,
  failed_delivery_count integer not null default 0,
  created_at timestamptz not null default now()
);

create table public.source_page_crawl_runs (
  id uuid primary key default gen_random_uuid(),
  crawl_run_id uuid not null references public.crawl_runs(id) on delete cascade,
  source_page_id text not null references public.source_pages(id) on delete restrict,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
  http_status integer,
  response_time_ms integer,
  item_count integer not null default 0,
  new_article_count integer not null default 0,
  sent_count integer not null default 0,
  failed_count integer not null default 0,
  error_code text,
  error_message text,
  created_at timestamptz not null default now()
);

create index articles_source_page_detected_idx on public.articles(source_page_id, first_detected_at desc);
create index deliveries_status_retry_idx on public.deliveries(status, next_retry_at);
create index deliveries_article_idx on public.deliveries(article_id);
create index delivery_attempts_delivery_idx on public.delivery_attempts(delivery_id, attempted_at desc);
create index source_page_crawl_runs_page_idx on public.source_page_crawl_runs(source_page_id, started_at desc);
create index crawl_runs_started_idx on public.crawl_runs(started_at desc);

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.admin_users where user_id = auth.uid()
  );
$$;

create or replace function public.upsert_article_candidate(
  p_source_page_id text,
  p_external_id text,
  p_title text,
  p_original_url text,
  p_normalized_url text,
  p_article_key text,
  p_published_at text
)
returns table (
  id uuid,
  source_page_id text,
  external_id text,
  title text,
  original_url text,
  normalized_url text,
  article_key text,
  published_at text,
  is_new boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  current_article public.articles%rowtype;
  inserted boolean := false;
begin
  select * into current_article
  from public.articles a
  where a.source_page_id = p_source_page_id
    and (
      (p_external_id is not null and a.external_id = p_external_id)
      or a.normalized_url = p_normalized_url
      or a.article_key = p_article_key
    )
  order by a.last_detected_at desc
  limit 1
  for update;

  if not found then
    insert into public.articles (
      source_page_id, external_id, title, original_url, normalized_url,
      article_key, published_at
    ) values (
      p_source_page_id, nullif(p_external_id, ''), p_title, p_original_url,
      p_normalized_url, p_article_key, nullif(p_published_at, '')
    ) returning * into current_article;
    inserted := true;
  else
    update public.articles
    set title = p_title,
        original_url = p_original_url,
        external_id = coalesce(nullif(p_external_id, ''), current_article.external_id),
        published_at = coalesce(nullif(p_published_at, ''), current_article.published_at),
        last_detected_at = now(),
        updated_at = now()
    where articles.id = current_article.id
    returning * into current_article;
  end if;

  return query select current_article.id, current_article.source_page_id,
    current_article.external_id, current_article.title, current_article.original_url,
    current_article.normalized_url, current_article.article_key,
    current_article.published_at, inserted;
end;
$$;

create or replace function public.ensure_delivery(
  p_article_id uuid,
  p_bot_profile text,
  p_target_chat_id text,
  p_delivery_generation integer
)
returns table (id uuid, created boolean)
language plpgsql
security definer
set search_path = public
as $$
declare
  delivery_id uuid;
  did_create boolean := false;
begin
  insert into public.deliveries (article_id, bot_profile, target_chat_id, delivery_generation)
  values (p_article_id, p_bot_profile, p_target_chat_id, p_delivery_generation)
  on conflict (article_id, bot_profile, target_chat_id, delivery_generation) do nothing
  returning deliveries.id into delivery_id;
  if delivery_id is not null then
    did_create := true;
  else
    select d.id into delivery_id from public.deliveries d
    where d.article_id = p_article_id
      and d.bot_profile = p_bot_profile
      and d.target_chat_id = p_target_chat_id
      and d.delivery_generation = p_delivery_generation;
  end if;
  return query select delivery_id, did_create;
end;
$$;

create or replace function public.mark_delivery_initial_sync(p_delivery_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  update public.deliveries
  set status = 'skipped_initial_sync', updated_at = now(), next_retry_at = null
  where id = p_delivery_id and status in ('pending', 'failed', 'retry_requested');
$$;

create or replace function public.set_source_page_initialized(
  p_source_page_id text,
  p_baseline_article_count integer
)
returns void
language sql
security definer
set search_path = public
as $$
  update public.source_pages
  set initialized = true,
      initialized_at = coalesce(initialized_at, now()),
      baseline_article_count = p_baseline_article_count,
      updated_at = now()
  where id = p_source_page_id;
$$;

create or replace function public.record_source_page_status(
  p_source_page_id text,
  p_success boolean,
  p_error_code text,
  p_error_message text
)
returns void
language sql
security definer
set search_path = public
as $$
  update public.source_pages
  set last_crawled_at = now(),
      last_success_at = case when p_success then now() else last_success_at end,
      last_error_at = case when p_success then last_error_at else now() end,
      last_error_code = case when p_success then null else p_error_code end,
      last_error_message = case when p_success then null else left(p_error_message, 500) end,
      consecutive_failure_count = case when p_success then 0 else consecutive_failure_count + 1 end,
      updated_at = now()
  where id = p_source_page_id;
$$;

create or replace function public.claim_delivery(p_delivery_id uuid)
returns table (delivery_id uuid, attempt_number integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  current_delivery public.deliveries%rowtype;
begin
  select * into current_delivery from public.deliveries
  where id = p_delivery_id
  for update;
  if not found or current_delivery.status in ('sent', 'dead_letter', 'sending', 'skipped_initial_sync') then
    return;
  end if;
  update public.deliveries
  set status = 'sending', attempt_count = attempt_count + 1, updated_at = now()
  where id = p_delivery_id;
  return query select p_delivery_id, current_delivery.attempt_count + 1;
end;
$$;

create or replace function public.mark_delivery_sent(
  p_delivery_id uuid,
  p_telegram_message_id text,
  p_http_status integer
)
returns void
language sql
security definer
set search_path = public
as $$
  update public.deliveries
  set status = 'sent', telegram_message_id = p_telegram_message_id,
      http_status = p_http_status, sent_at = now(), next_retry_at = null, updated_at = now()
  where id = p_delivery_id;
$$;

create or replace function public.mark_delivery_failed(
  p_delivery_id uuid,
  p_status public.delivery_status,
  p_error_code text,
  p_error_message text,
  p_next_retry_at timestamptz,
  p_http_status integer
)
returns void
language sql
security definer
set search_path = public
as $$
  update public.deliveries
  set status = p_status, last_error_code = p_error_code,
      last_error_message = left(p_error_message, 500), next_retry_at = p_next_retry_at,
      http_status = p_http_status, updated_at = now()
  where id = p_delivery_id;
$$;

create or replace function public.recover_stale_deliveries()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare recovered integer;
begin
  with changed as (
    update public.deliveries
    set status = 'failed', next_retry_at = now(), updated_at = now(),
        last_error_code = 'stale_sending_recovered'
    where status = 'sending' and updated_at < now() - interval '15 minutes'
    returning id
  )
  select count(*) into recovered from changed;
  return recovered;
end;
$$;

create or replace function public.request_delivery_retry(p_delivery_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_admin() then
    raise exception 'admin access required';
  end if;
  update public.deliveries
  set status = 'retry_requested', next_retry_at = now(), updated_at = now()
  where id = p_delivery_id and status in ('failed', 'dead_letter', 'retry_requested');
end;
$$;

create or replace function public.force_retry_delivery(p_delivery_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  update public.deliveries
  set status = 'retry_requested', next_retry_at = now(), updated_at = now()
  where id = p_delivery_id and status in ('pending', 'failed', 'dead_letter', 'retry_requested');
$$;

alter table public.admin_users enable row level security;
alter table public.source_pages enable row level security;
alter table public.articles enable row level security;
alter table public.deliveries enable row level security;
alter table public.delivery_attempts enable row level security;
alter table public.crawl_runs enable row level security;
alter table public.source_page_crawl_runs enable row level security;

create policy "admins can read admin users" on public.admin_users
  for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy "admins can read source pages" on public.source_pages
  for select to authenticated using (public.is_admin());
create policy "admins can update source pages" on public.source_pages
  for update to authenticated using (public.is_admin()) with check (public.is_admin());
create policy "admins can read articles" on public.articles
  for select to authenticated using (public.is_admin());
create policy "admins can read deliveries" on public.deliveries
  for select to authenticated using (public.is_admin());
create policy "admins can read delivery attempts" on public.delivery_attempts
  for select to authenticated using (public.is_admin());
create policy "admins can read crawl runs" on public.crawl_runs
  for select to authenticated using (public.is_admin());
create policy "admins can read source page crawl runs" on public.source_page_crawl_runs
  for select to authenticated using (public.is_admin());

revoke execute on function public.upsert_article_candidate(text, text, text, text, text, text, text) from public, anon, authenticated;
revoke execute on function public.ensure_delivery(uuid, text, text, integer) from public, anon, authenticated;
revoke execute on function public.mark_delivery_initial_sync(uuid) from public, anon, authenticated;
revoke execute on function public.set_source_page_initialized(text, integer) from public, anon, authenticated;
revoke execute on function public.record_source_page_status(text, boolean, text, text) from public, anon, authenticated;
revoke execute on function public.claim_delivery(uuid) from public, anon, authenticated;
revoke execute on function public.mark_delivery_sent(uuid, text, integer) from public, anon, authenticated;
revoke execute on function public.mark_delivery_failed(uuid, public.delivery_status, text, text, timestamptz, integer) from public, anon, authenticated;
revoke execute on function public.recover_stale_deliveries() from public, anon, authenticated;
revoke execute on function public.force_retry_delivery(uuid) from public, anon, authenticated;
grant execute on function public.request_delivery_retry(uuid) to authenticated;
grant execute on function public.upsert_article_candidate(text, text, text, text, text, text, text) to service_role;
grant execute on function public.ensure_delivery(uuid, text, text, integer) to service_role;
grant execute on function public.mark_delivery_initial_sync(uuid) to service_role;
grant execute on function public.set_source_page_initialized(text, integer) to service_role;
grant execute on function public.record_source_page_status(text, boolean, text, text) to service_role;
grant execute on function public.claim_delivery(uuid) to service_role;
grant execute on function public.mark_delivery_sent(uuid, text, integer) to service_role;
grant execute on function public.mark_delivery_failed(uuid, public.delivery_status, text, text, timestamptz, integer) to service_role;
grant execute on function public.recover_stale_deliveries() to service_role;
grant execute on function public.force_retry_delivery(uuid) to service_role;
