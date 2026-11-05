"""One-minute check that the configured model setup works, before running anything bigger.

    make llm-smoke

Reads the same SG_* settings as the app, prints the effective configuration (never keys), then makes one tiny chat call
and one embedding call through the gateway. Exit 0 = both worked. Uses no database and no student data.
"""

import sys
import time

from app.config import get_settings
from app.llm.errors import ExternalEndpointRefused
from app.llm.gateway import get_chat_model, get_embedding_client
from app.llm.guard import describe_llm, invoke_guarded


def main() -> int:
    s = get_settings()
    for line in describe_llm(s):
        print(line)
    print(f"embeddings: {s.embedding_provider} model={s.embedding_model}")
    try:
        t0 = time.perf_counter()
        out = invoke_guarded(get_chat_model("faq", s), "Reply with the single word: ready", s)
        print(f"chat ok in {time.perf_counter() - t0:.1f}s (first call includes model load): {str(out.content).strip()[:60]!r}")
        t0 = time.perf_counter()
        vec = get_embedding_client(s).embed_query("What is the fee for the Python course?")
        print(f"embedding ok in {time.perf_counter() - t0:.1f}s, {len(vec)} dimensions")
    except ExternalEndpointRefused as e:
        print(f"REFUSED: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: {type(e).__name__}. Is Ollama running and the model pulled? (ollama pull {s.llm_model}; ollama pull {s.embedding_model})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
