#Desk test for the vision analysis — runs analyze() on a local PDF, no Flask,
#no Procore, no session needed. Use it to sanity-check the prompt and the model
#before wiring a whole billing period through the UI.
#
#   1. save an invoice PDF locally (the /sub-chaser/phase0/pdf endpoint gives
#      you one straight from Procore — open it in the browser and save it)
#   2. python -m sub_chaser.test_vision "C:\path\to\invoice.pdf"
#
#Needs ANTHROPIC_API_KEY in the repo-root .env.

import json
import sys

from dotenv import load_dotenv
load_dotenv()

from sub_chaser.vision import analyze, MODEL, EFFORT


def main():
    if len(sys.argv) < 2:
        print(__doc__ or "usage: python -m sub_chaser.test_vision <path-to-pdf>")
        return 1

    path = sys.argv[1]
    with open(path, "rb") as f:
        pdf_bytes = f.read()

    print(f"file:  {path}  ({round(len(pdf_bytes) / 1024)} KB)")
    print(f"model: {MODEL}  (effort: {EFFORT})")
    print("analyzing…")

    verdict = analyze(pdf_bytes)
    print(json.dumps(verdict, indent=2))

    compliant = verdict["aia"] == "yes" and verdict["lien"] == "yes"
    print("\nverdict:", "COMPLIANT" if compliant else "NEEDS CHASING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
