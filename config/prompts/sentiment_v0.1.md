# Sentiment Prompt v0.1

Use only external information already attached to the run input snapshot.

Every item must include source, published_at, fetched_at, and credibility
(`HIGH`, `MEDIUM`, `LOW`, or `UNKNOWN`). Sentiment alone cannot upgrade a setup
from `NO_TRADE` to `CONSIDER_LONG` or `CONSIDER_SHORT`.
