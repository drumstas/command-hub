create table if not exists members(
  id serial primary key,
  key text unique not null,
  display_name text not null,
  role text not null check (role in ('owner','member')),
  telegram_chat_id bigint unique,
  discord_user_id text unique,
  link_code text,
  preferred_channel text check (preferred_channel in ('telegram','discord'))
);

create table if not exists tasks(
  id serial primary key,
  title text not null,
  details text,
  assignee_id int not null references members(id),
  created_by int not null references members(id),
  due_at timestamptz,
  status text not null default 'open' check (status in ('open','done','cancelled')),
  done_at timestamptz,
  reminded_at timestamptz,
  overdue_notified_on date,
  created_at timestamptz not null default now()
);

create table if not exists documents(
  id serial primary key,
  kind text not null check (kind in ('message','quote','proforma')),
  title text not null,
  body_md text not null,
  status text not null default 'draft' check (status in ('draft','approved','void')),
  number text unique,
  created_by int references members(id),
  approved_by int references members(id),
  created_at timestamptz not null default now()
);

create table if not exists processed_events(
  source text,
  event_id text,
  at timestamptz default now(),
  primary key(source, event_id)
);

create table if not exists audit_log(
  id bigserial primary key,
  at timestamptz default now(),
  member_id int,
  action text,
  payload jsonb
);

create table if not exists llm_calls(
  id bigserial primary key,
  at timestamptz default now(),
  purpose text,
  model text,
  in_tokens int,
  out_tokens int,
  ok boolean
);
