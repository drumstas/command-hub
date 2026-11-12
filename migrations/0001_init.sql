create table if not exists members(
  id serial primary key,
  key text unique not null,
  display_name text not null,
  role text not null check (role in ('owner','member')),
  telegram_chat_id bigint unique,
  discord_user_id text unique,
  link_code text unique,
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
  discord_notified_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists processed_events(
  source text not null,
  event_id text not null,
  at timestamptz not null default now(),
  primary key(source, event_id)
);

create table if not exists audit_log(
  id bigserial primary key,
  at timestamptz not null default now(),
  member_id int,
  action text not null,
  payload jsonb not null
);
