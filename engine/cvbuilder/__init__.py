"""Conversational CV builder — a self-contained module.

A chatbot captures the user's GOAL (free text), optionally assesses an imported
CV, then interviews them to fill a structured CVData schema (probing for
quantifiable results), and renders a clean one-page CV. Built for people with
little CV experience (early-career, school leavers) as well as professionals.

Isolated from the job-applicator engine so it can later be lifted into the
separate voice-CV product. The LLM produces DATA (CVData); a deterministic
template renders the layout.
"""
