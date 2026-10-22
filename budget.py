"""Étape 14 : total cumulé des dépenses API, pour suivre le budget restant.

Additionne les coûts enregistrés dans les logs :
- logs/runs/*/run_summary.json   (parties du harness)
- logs/vision_test/*/results.json (tests de vision)
- logs/budget_extra.json          (facultatif : dépenses absentes des logs)
      [{"date": "2026-10-20", "what": "premiers tests de vision", "cost_usd": 0.05}]

C'est une estimation (prix de llm.PRICES) : la console Mistral fait foi.

    python budget.py
"""

import json
from pathlib import Path

from llm import BUDGET_USD

ROOT = Path(__file__).parent
LOGS = ROOT / "logs"
EXTRA = LOGS / "budget_extra.json"


def entries():
    """Toutes les dépenses connues : (date, source, appels, coût)."""
    found = []
    for path in sorted((LOGS / "runs").glob("*/run_summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        if summary.get("cost_usd"):
            what = f"partie ({summary.get('end')}, étage {summary.get('deepest_floor', '?')})"
            if summary.get("reconstructed"):
                what += ", reconstituée"
            found.append((path.parent.name, what, summary.get("calls", 0), summary["cost_usd"]))
    for path in sorted((LOGS / "vision_test").glob("*/results.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        if rows:
            found.append((path.parent.name, "test de vision", len(rows), sum(r["cost_usd"] for r in rows)))
    if EXTRA.exists():
        for item in json.loads(EXTRA.read_text(encoding="utf-8")):
            found.append((item.get("date", "?"), item.get("what", "hors logs"), item.get("calls", 0), item["cost_usd"]))
    return found


def spent():
    return sum(cost for _, _, _, cost in entries())


def main():
    rows = entries()
    print(f"{'Date':<17} {'Source':<42} {'Appels':>6} {'Coût':>9}")
    for date, what, calls, cost in rows:
        print(f"{date:<17} {what:<42} {calls:>6} {cost:>8.4f} $")
    total = sum(cost for _, _, _, cost in rows)
    calls = sum(c for _, _, c, _ in rows)
    print(f"\nTotal : {calls} appels, {total:.4f} $ sur {BUDGET_USD:.0f} $ -> reste {BUDGET_USD - total:.2f} $"
          f" (soit ~{(BUDGET_USD - total) / 0.003:.0f} appels à ~0,003 $)")
    if not EXTRA.exists():
        print("Les dépenses absentes des logs peuvent être ajoutées dans logs/budget_extra.json."
              " La console Mistral fait foi.")


if __name__ == "__main__":
    main()
