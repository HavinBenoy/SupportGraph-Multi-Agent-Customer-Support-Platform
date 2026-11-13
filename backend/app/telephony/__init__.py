"""M8: the telephone channel.

Layers (each replaceable without touching the others; docs/M8.md section 4):
  base.py      the provider contract: how a call starts, what events arrive, which control messages exist
  edesy.py     Edesy Voice API media streams (the approved, recommended provider)
  media.py     audio conversion and buffering between the provider's line format and the voice session (resampling, 20 ms framing, flush on barge-in)
  identity.py  caller-number normalisation, account lookup, masking, phone-support PINs with lockout
  (route)      app/api/routes/telephony.py wires one call: provider WebSocket <-> media bridge <-> call hub (M4/M4.1/M5) <-> VoiceSession <-> LangGraph
The LangGraph workflow, VoiceSession and the call hub do not import anything from this package."""
