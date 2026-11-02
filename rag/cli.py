"""Ask questions from the terminal.

    python -m rag.cli "Can I take my bike on the train?"
    python -m rag.cli --no-llm "What is a Penalty Fare?"
    python -m rag.cli            (interactive)
"""
import argparse
import textwrap

from . import config, ollama_client
from .answer import answer
from .retrieve import Retriever


def show(a, verbose: bool):
    print("\n" + a.text + "\n")
    for note in a.notes:
        print(f"  ! {note}")
    print(f"  [{a.generator}, retrieval: {a.retrieval_mode}]")
    for s in a.sources:
        if s.cited or verbose:
            mark = "*" if s.cited else " "
            print(f" {mark}[{s.n}] {s.doc_short} — {s.label} ({s.pages})")
            if verbose:
                print(textwrap.indent(textwrap.fill(s.text[:400] + "…", 100), "       "))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--no-llm", action="store_true", help="extractive answers only")
    ap.add_argument("--mode", default="auto", choices=["auto", "bm25", "dense", "hybrid"])
    ap.add_argument("-k", type=int, default=config.TOP_K)
    ap.add_argument("-v", "--verbose", action="store_true", help="show passage text")
    args = ap.parse_args()

    retriever = Retriever.load()
    use_llm = not args.no_llm and ollama_client.has_model(config.CHAT_MODEL)
    if not args.no_llm and not use_llm:
        print(f"(Ollama or model '{config.CHAT_MODEL}' not available — using extractive answers.)")

    if args.question:
        show(answer(" ".join(args.question), retriever, args.k, args.mode, use_llm), args.verbose)
        return
    while True:
        try:
            q = input("Question (empty to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        show(answer(q, retriever, args.k, args.mode, use_llm), args.verbose)


if __name__ == "__main__":
    main()
