"""Score oweit's extraction on eval/messages.jsonl with real Claude calls.

    uv run python eval/run_eval.py            # full run, writes eval/results/<date>-<model>.json
    uv run python eval/run_eval.py --no-llm   # prefilter numbers only, no API key needed

Two arms over the same 202 messages:
  pipeline   regex prefilter, then Claude only on the messages that pass (what oweit does)
  no_filter  Claude on every message (what the filter saves, and what it costs in recall)
Messages that pass the filter are sent once and the answer is reused by both arms.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from oweit import extract, prefilter
from oweit.sources import Message

HERE = Path(__file__).parent
PRICE_IN, PRICE_OUT = 4.00, 20.00  # USD per 1M tokens for claude-opus-5-5
TITLES = {"prof", "dr", "mr", "mrs", "ms", "sir", "madam", "the"}


def load(split: str) -> list[dict]:
    rows = [json.loads(line) for line in (HERE / "messages.jsonl").open(encoding="utf-8")]
    return [r for r in rows if split == "all" or r["split"] == split]


def as_message(r: dict) -> Message:
    ts = datetime.fromisoformat(r["ts"])
    return Message(r["source"], r["chat"], ts, "Naman", r["text"], True, r["to"], r["context"])


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - TITLES


def name_ok(pred: str, golds: list[str]) -> bool:
    return any(_words(pred) & _words(g) for g in golds)


def score(rows: list[dict], preds: dict[str, list[dict]]) -> dict:
    tp = fp = fn = pairs = to_ok = date_ok = 0
    for r in rows:
        gold = sorted(r["gold"], key=lambda x: x["due"] or "~")
        pred = sorted(preds.get(r["id"], []), key=lambda x: x["due"] or "~")
        k = min(len(gold), len(pred))
        tp, fp, fn = tp + k, fp + len(pred) - k, fn + len(gold) - k
        # shortcut: promises inside one message are paired in due-date order, not by meaning;
        # fine while 3 of 202 messages hold two promises, needs real matching beyond that
        for g, p in zip(gold, pred, strict=False):
            pairs += 1
            to_ok += name_ok(p["to"], g["to"])
            date_ok += p["due"] == g["due"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "recipient_accuracy": round(to_ok / pairs, 3) if pairs else None,
        "date_accuracy": round(date_ok / pairs, 3) if pairs else None,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def filter_stats(rows: list[dict], passed: set[str]) -> dict:
    gold = sum(len(r["gold"]) for r in rows)
    kept = sum(len(r["gold"]) for r in rows if r["id"] in passed)
    n_pass = sum(r["id"] in passed for r in rows)
    return {
        "messages": len(rows),
        "sent_to_claude": n_pass,
        "llm_calls_avoided": round(1 - n_pass / len(rows), 3),
        "filter_recall": round(kept / gold, 3) if gold else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["dev", "test", "all"])
    ap.add_argument("--no-llm", action="store_true", help="prefilter numbers only")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    rows = load(args.split)
    passed = {r["id"] for r in rows if prefilter.is_candidate(r["text"])}
    if args.no_llm:
        for split in ("dev", "test", "all"):
            sub = [r for r in rows if split == "all" or r["split"] == split]
            if sub:
                print(split, filter_stats(sub, passed))
        missed = [r for r in rows if r["gold"] and r["id"] not in passed]
        noisy = [r for r in rows if not r["gold"] and r["id"] in passed]
        print(f"\npromise messages the filter drops ({len(missed)}):")
        for r in missed:
            print(f"  {r['id']} [{r['split']}] {r['text']!r}")
        print(f"\nno-promise messages it lets through ({len(noisy)}):")
        for r in noisy:
            print(f"  {r['id']} [{r['split']}] {r['text']!r}")
        return 0

    import anthropic

    client = anthropic.Anthropic()

    def run(r: dict) -> tuple[str, extract.Result]:
        return r["id"], extract.extract(as_message(r), client)

    with ThreadPoolExecutor(args.workers) as pool:
        results = dict(pool.map(run, rows))

    def preds(ids):
        return {
            i: [
                {
                    "to": p.to,
                    "what": p.what,
                    "due_phrase": p.due_phrase,
                    "due": p.due.isoformat() if p.due else None,
                }
                for p in results[i].promises
            ]
            for i in ids
        }

    pipeline = preds(passed)
    no_filter = preds(results)
    tok_in = sum(r.input_tokens for r in results.values())
    tok_out = sum(r.output_tokens for r in results.values())
    pipe_in = sum(results[i].input_tokens for i in passed)
    pipe_out = sum(results[i].output_tokens for i in passed)

    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": extract.MODEL,
        "effort": "low",
        "dataset": "eval/messages.jsonl (hand-written, hand-labelled)",
        "refusals": sum(r.refused for r in results.values()),
        "cost_usd": {
            "pipeline": round((pipe_in * PRICE_IN + pipe_out * PRICE_OUT) / 1e6, 3),
            "no_filter": round((tok_in * PRICE_IN + tok_out * PRICE_OUT) / 1e6, 3),
        },
        "tokens": {"input": tok_in, "output": tok_out},
        "splits": {},
    }
    for split in ("test", "dev", "all"):
        sub = [r for r in rows if split == "all" or r["split"] == split]
        if not sub:
            continue
        report["splits"][split] = {
            "prefilter": filter_stats(sub, passed),
            "pipeline": score(sub, pipeline),
            "no_filter": score(sub, no_filter),
            "pipeline_by_lang": {
                lang: score([r for r in sub if r["lang"] == lang], pipeline)
                for lang in ("en", "hinglish")
            },
        }
    report["predictions"] = {
        r["id"]: {"passed_filter": r["id"] in passed, "pred": no_filter[r["id"]], "gold": r["gold"]}
        for r in rows
    }
    out = HERE / "results" / f"{date.today()}-{extract.MODEL}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {out}")
    for split, s in report["splits"].items():
        p, n, f = s["pipeline"], s["no_filter"], s["prefilter"]
        print(
            f"\n[{split}] {f['messages']} messages, {f['sent_to_claude']} sent to Claude "
            f"({f['llm_calls_avoided']:.0%} avoided), filter recall {f['filter_recall']:.0%}"
        )
        print("arm        precision  recall  f1     recipient  date")
        for name, m in (("pipeline", p), ("no_filter", n)):
            print(
                f"{name:<10} {m['precision']:<10} {m['recall']:<7} {m['f1']:<6} "
                f"{m['recipient_accuracy']:<10} {m['date_accuracy']}"
            )
    print(
        f"\ncost: pipeline ${report['cost_usd']['pipeline']}, "
        f"no_filter ${report['cost_usd']['no_filter']}, refusals {report['refusals']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
