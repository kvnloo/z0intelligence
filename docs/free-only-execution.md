# Free-only worker execution

The production worker policy defaults to `free_only=true`. A request cannot override it with false. Eligibility requires an exact validated provider/model pair in the production manifest; free credits and guessed `:free` names are insufficient. OpenRouter requests additionally impose zero prompt/completion/request price ceilings and disable provider-side fallback.

Exhausted, capped, unhealthy or unvalidated free routes produce native fallback or explicit refusal. No paid overflow is permitted. Cerebras experimental trial/credit cohorts do not authorize automatic production worker use. Jev's separately authorized existing verification function is the narrow paid exception; its receipts explicitly say so.

Free-plan quotas use KERD projection over canonical receipts, with conservative reservations for pending/uncertain calls and provider reset/remaining headers. A date change is not itself a quota refill. Missing KERD support blocks quota-controlled admission.

Transport success is not function quality, and function quality is not measured offload benefit. Both are required before automatic worker promotion. The current generic-worker cohort did not establish positive benefit.
