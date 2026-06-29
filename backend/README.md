# Backend (brain)

TODO: a cleaned fork of the Flask outreach app, providing:
- campaigns + variable substitution (salvage existing logic)
- ONE database as source of truth (kill SQLite/Sheets/in-memory split)
- SendChannel interface (ServerChannel now, BrowserChannel later)
- persistent reply-aware follow-up scheduler
- auth on the dashboard

Personalization is provided by ../shared/generator (already built).
