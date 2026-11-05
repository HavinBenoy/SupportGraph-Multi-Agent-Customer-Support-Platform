"""Benchmark kit (milestone 0): measures candidate local models on THIS machine so the model choice is data, not a guess.

Needs no database and no network beyond the local Ollama server. Inputs are the synthetic QSpiders documents and the
synthetic evaluation datasets already in the repo; no student data is read or written. Results are plain JSON files
(`evals/bench/*.json`) plus a Markdown summary, safe to paste back for review.
"""
