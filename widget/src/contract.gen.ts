// Generated from heed's Pydantic models by `python -m heed.widget contract`.
// Do not edit: change heed/base.py or heed/config.py and regenerate.
export const CONTRACT = {
  "fields": {
    "title": "title",
    "body": "body",
    "category": "category",
    "page_url": "page_url",
    "env": "env",
    "anon_id": "anon_id",
    "screenshot": "screenshot",
    "bot_token": "bot_token",
    "honeypot": "hp_website"
  },
  "categories": [
    "bug",
    "feature",
    "question",
    "other"
  ],
  "limits": {
    "title_chars": 300,
    "body_chars": 20000,
    "url_chars": 2000,
    "short_chars": 500,
    "extra_items": 32,
    "extra_key_chars": 64,
    "anon_id_chars": 128,
    "field_bytes": 100000,
    "screenshot_bytes": 5000000,
    "screenshot_pixels": 25000000
  },
  "patterns": {
    "anon_id": "^[A-Za-z0-9_.:-]+$"
  },
  "statuses": {
    "received": "received",
    "held": "held"
  }
} as const;
