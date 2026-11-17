#!/usr/bin/env python3
"""List chat models each free-capable provider exposes right now, with its free-ness class.

Run under dsh-env-exec so keys come from the vault. Writes catalog.json (no secrets).
Free classes: free (no charge, per-model limits), free-credit (monthly/trial credit that runs out),
free-public (free but prompts may be used for training: scrubbed, non-private data only).
"""
import json, os, ssl, sys, time, urllib.request

CTX = ssl.create_default_context()
UA = {"User-Agent": "z0int-vllm-sr-catalog/1"}

PROVIDERS = {
    # name: (base_url, key env, free class, model filter)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "free", None),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "free-credit", None),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "free", None),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "free", lambda m: m["id"].endswith(":free")),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "free-public", None),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "free", None),
    "sambanova": ("https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY", "free", None),
    "vercel": ("https://ai-gateway.vercel.sh/v1", "AI_GATEWAY_API_KEY", "free-credit", None),
    "nous": ("https://inference-api.nousresearch.com/v1", "NOUS_API_KEY", "free-credit", None),
    "cohere": ("https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY", "free-credit", None),
}
SKIP = ("embed", "rerank", "whisper", "tts", "guard", "moderation", "vision-only", "image", "audio", "transcribe",
        "speech", "safety", "clip", "parse", "ocr", "retriever", "nemoretriever", "reward", "deplot", "kosmos")


def get(url, key):
    req = urllib.request.Request(url, headers={**UA, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read())


def main(out):
    catalog = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "providers": {}}
    for name, (base, env, klass, keep) in PROVIDERS.items():
        key = os.environ.get(env)
        row = {"base_url": base, "key_env": env, "free_class": klass, "models": []}
        if not key:
            row["error"] = "no key"
        else:
            try:
                data = get(base + "/models", key)
                models = data.get("data", data if isinstance(data, list) else [])
                for m in models:
                    mid = m.get("id") or m.get("name", "")
                    if keep and not keep(m):
                        continue
                    if any(s in mid.lower() for s in SKIP):
                        continue
                    row["models"].append({"id": mid, "context": m.get("context_length") or m.get("context_window")})
            except Exception as e:  # recorded, never fatal: a provider being down is data
                row["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        catalog["providers"][name] = row
        print(f"{name:11s} {klass:12s} {len(row['models']):4d} models {row.get('error', '')}", flush=True)
    json.dump(catalog, open(out, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "catalog.json")
