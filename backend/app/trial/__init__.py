"""M5: Trial Verification Mode: human-supervised AI support for a safer transition to autonomous customer service.

  settings.py    the administrator's switch + disclosure/consent/retention configuration (persisted, versioned, audited)
  consent.py     the caller's per-call recording consent (explicit, versioned, withdrawable)
  recording.py   the recording-provider interface, the local-disk provider, and the recorder fed by the live call (fails closed)
  retention.py   what is eligible to be deleted, the idempotent purge job, and the status the admin screen shows
  metrics.py     trial performance numbers computed from persisted rows only
  call.py        the per-call glue: spoken disclosure, consent messages from the caller's browser, starting/stopping the recorder

Live monitoring is NOT recording: staff may listen to a call without anything being stored. Audio is stored only if (1) a recording provider is configured,
(2) an administrator offers recording, and (3) the caller explicitly consents for that call. Nothing here is a legal compliance claim: the organisation must
review the privacy, recording and consent rules that apply to it before real-world use (docs/M5.md)."""
