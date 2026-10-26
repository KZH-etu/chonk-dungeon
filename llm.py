"""Appels à l'API Mistral : clé, prix, limite de débit et nouvel essai après un 429.

Partagé par vision_test.py (étape 7) et le cerveau du harness (brain.py, étape 11).
"""

import os
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).parent

# Prix en $ par million de tokens (à confirmer sur mistral.ai/pricing)
PRICES = {
    "mistral-large": (1.36, 4.18),
    "mistral-medium": (0.40, 2.00),
    "mistral-small": (0.10, 0.30),
}
BUDGET_USD = 10.0
TOKENS_PER_MINUTE = 20_000


def env_value(name):
    """Valeur d'une variable : environnement d'abord, sinon fichier .env."""
    value = os.environ.get(name)
    if value:
        return value
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_api_key():
    key = env_value("MISTRAL_API_KEY")
    if key:
        return key
    raise SystemExit("MISTRAL_API_KEY introuvable (variable d'environnement ou .env)")


def default_model():
    return env_value("CHONK_MODEL") or "mistral-medium-latest"


def prices_for(model):
    for prefix, price in PRICES.items():
        if model.startswith(prefix):
            return price
    return PRICES["mistral-large"]


def cost_usd(model, usage):
    price_in, price_out = prices_for(model)
    return (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6


def make_client():
    from mistralai.client import Mistral
    return Mistral(api_key=load_api_key())


SERVER_ERRORS = {500, 502, 503, 504}  # erreurs passagères du serveur : on réessaie, comme pour la coupure réseau
SERVER_RETRIES = 3


def network_error(error):
    """Coupure réseau ou délai dépassé (httpx2 est la bibliothèque HTTP du SDK mistralai)."""
    try:
        import httpx2
    except ImportError:
        return False
    return isinstance(error, httpx2.TransportError)


def complete(client, model, messages, max_tokens, response_format=None, reasoning=None, temperature=0.1):
    """Appel avec attente et nouvel essai en cas de 429 (limite de tokens/min), d'erreur 5xx ou de coupure
    réseau. Renvoie (réponse, secondes)."""
    extra = {"reasoning_effort": reasoning} if reasoning else {}
    if response_format:
        extra["response_format"] = response_format
    server_failures = rate_limited = 0
    while True:
        try:
            start = time.perf_counter()
            response = client.chat.complete(model=model, messages=messages, temperature=temperature,
                                            max_tokens=max_tokens, **extra)
            return response, time.perf_counter() - start
        except Exception as error:  # le SDK lève une erreur générique avec le code HTTP
            status = getattr(error, "status_code", None)
            if status in SERVER_ERRORS or network_error(error):
                server_failures += 1
                if server_failures > SERVER_RETRIES:
                    raise RuntimeError(f"Erreur du serveur ou du réseau après {SERVER_RETRIES} essais : {error}")
                print(f"   {status or type(error).__name__} : nouvel essai dans {2 * server_failures} s")
                time.sleep(2 * server_failures)
                continue
            if status != 429:
                raise
            print_rate_limits(error)
            headers = getattr(error, "headers", None) or {}
            if any(k.lower().startswith("x-ratelimit-limit") and v.strip() == "0" for k, v in headers.items()):
                raise SystemExit("Limite à 0 : la clé n'a droit à aucun appel. Activer une offre API dans la console Mistral.")
            rate_limited += 1
            if rate_limited == 5:
                raise RuntimeError("Toujours 429 après 5 essais : voir les en-têtes de limite ci-dessus.")
            wait = 15 * rate_limited
            print(f"   429 (limite de débit) : attente {wait} s")
            time.sleep(wait)


def print_rate_limits(error):
    """Affiche les en-têtes de limite renvoyés avec le 429 (quelle limite bloque, et quand elle se libère)."""
    headers = getattr(error, "headers", None) or {}
    limits = {k: v for k, v in headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    print(f"   Réponse : {getattr(error, 'body', '')}")
    if limits:
        for key, value in sorted(limits.items()):
            print(f"     {key}: {value}")
    else:
        print("     (aucun en-tête de limite renvoyé)")


class RateLimiter:
    """Attend avant un appel si les tokens des 60 dernières secondes dépassent la limite."""

    def __init__(self, tokens_per_minute=TOKENS_PER_MINUTE):
        self.limit = tokens_per_minute * 0.9  # marge
        self.calls = deque()  # (heure, tokens)

    def wait(self, expected_tokens):
        while True:
            now = time.monotonic()
            while self.calls and now - self.calls[0][0] > 60:
                self.calls.popleft()
            used = sum(tokens for _, tokens in self.calls)
            if used + expected_tokens <= self.limit or not self.calls:
                return
            pause = min(5.0, 60 - (now - self.calls[0][0]) + 0.1)
            print(f"   limite de tokens/min : pause {pause:.0f} s")
            time.sleep(pause)

    def record(self, tokens):
        self.calls.append((time.monotonic(), tokens))
