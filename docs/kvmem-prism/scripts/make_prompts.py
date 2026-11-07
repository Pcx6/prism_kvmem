#!/usr/bin/env python3
"""Generate deterministic long-context prompts (no external downloads)."""
import argparse
import pathlib

FILLER = "The quick brown fox jumps over the lazy dog. "
PARA = ("Section {n}. The system keeps a bounded working set and pages cold blocks "
        "to host memory. Retrieval scores blocks with the query span. ")


def build(n_tokens: int, secret: str | None = None) -> str:
    target = n_tokens * 4
    out = []
    size = 0
    n = 0
    while size < target:
        n += 1
        line = PARA.format(n=n) + FILLER * (n % 7 + 1) + "\n"
        if secret and size > target * 0.6 and secret not in "".join(out[-40:]):
            line = "NOTE: the access code is " + secret + ".\n" + line
        out.append(line)
        size += len(line)
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    d = pathlib.Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt_128k.txt").write_text(build(117789), encoding="utf-8")
    (d / "prompt_250k.txt").write_text(build(250000), encoding="utf-8")
    q = build(100000, "ORANGE-FALCON-42")
    q += "\nQuestion: what is the access code? Answer with the code only.\n"
    (d / "prompt_quality_100k.txt").write_text(q, encoding="utf-8")
    print("wrote", d / "prompt_128k.txt", d / "prompt_250k.txt", d / "prompt_quality_100k.txt")


if __name__ == "__main__":
    main()
