"""Foundry Evaluations on the half-time eval's output: every aired script judged for groundedness
against the facts the segment was built from (Azure AI Evaluation's GroundednessEvaluator, a
model judge scoring 1 to 5). A second opinion beside the Verifier, which checks the numbers in
code. The run is logged to the Foundry project when the identity is allowed to.

    python -m rundown.evals.foundry docs/evals/rundown.json --out docs/evals/foundry-groundedness.json

Needs AZURE_OPENAI_* (the judge) and, to log to Foundry, AZURE_AI_PROJECT_ENDPOINT plus a credential.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
from pathlib import Path


def rows(eval_json: dict) -> list[dict]:
    out = []
    for run in eval_json["runs"]:
        for system in ("agents", "rules"):
            for a in run.get(system, {}).get("aired", []):
                if not a.get("script"):
                    continue
                out.append(dict(seed=run["seed"], system=system, title=a["title"],
                                query=f"Half-time talking point: {a['headline']}",
                                response=" ".join(a["script"]),
                                context=f"Talking point at {a['minute']}': {a['headline']}\n"
                                        "Facts from the stats engine:\n" + "\n".join(a["facts"])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_json", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    from azure.ai.evaluation import AzureOpenAIModelConfiguration, GroundednessEvaluator, evaluate

    model = AzureOpenAIModelConfiguration(azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                                          api_key=os.environ["AZURE_OPENAI_API_KEY"],
                                          azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
                                          api_version="2024-10-21")
    data = rows(json.loads(a.eval_json.read_text()))
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kw = dict(data=f.name, evaluators={"groundedness": GroundednessEvaluator(model)},
              evaluator_config={"groundedness": {"column_mapping": {
                  "query": "${data.query}", "response": "${data.response}", "context": "${data.context}"}}},
              evaluation_name="rundown-groundedness")
    logged, log_error = None, None
    if os.environ.get("AZURE_AI_PROJECT_ENDPOINT"):
        try:
            res = evaluate(azure_ai_project=os.environ["AZURE_AI_PROJECT_ENDPOINT"], **kw)
            logged = res.get("studio_url")
        except Exception as e:  # not allowed to log to the project: say so, still score locally
            log_error = f"{type(e).__name__}: {str(e)[:300]}"
            res = evaluate(**kw)
    else:
        res = evaluate(**kw)

    scored = [dict(r, score=out.get("outputs.groundedness.groundedness"),
                   reason=out.get("outputs.groundedness.groundedness_reason")) for r, out in zip(data, res["rows"])]
    by = {}
    for r in scored:
        by.setdefault(r["system"], []).append(dict(seed=r["seed"], title=r["title"], score=r["score"], reason=r["reason"]))
    summary = {}
    for system, items in by.items():
        scores = [i["score"] for i in items if i["score"] is not None]
        summary[system] = dict(segments=len(items), scored=len(scores),
                               mean=round(statistics.mean(scores), 2) if scores else None,
                               at_least_4=sum(1 for s in scores if s >= 4),
                               below_4=[i for i in items if i["score"] is not None and i["score"] < 4])
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "below_4"} for k, v in summary.items()}, indent=1))
    print("logged to Foundry:", logged or f"no ({log_error})")
    if a.out:
        a.out.write_text(json.dumps(dict(summary=summary, foundry_url=logged, log_error=log_error,
                                         judge=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
                                         rows=scored),
                                    indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
