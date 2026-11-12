create table if not exists pending_tasks(
  token text primary key,
  source_event_id text not null unique,
  requester_id int not null references members(id),
  assignee_id int not null references members(id),
  title text not null,
  details text,
  due_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists used_link_codes(
  code text primary key,
  member_key text not null,
  platform text not null check (platform in ('telegram','discord')),
  used_at timestamptz not null default now()
);
