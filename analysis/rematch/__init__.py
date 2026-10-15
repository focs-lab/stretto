"""Rematch: sound prediction of alternative message matchings from one MPI trace.

WP0 prototype. Stdlib only. Modules:
  trace     event model, per-rank JSONL loading, observed matching reconstruction
  matching  compatibility, candidate alternative matches, observed happens-before
  horizon   causal-closure horizon for a set of changed matches
  feasible  feasibility of an alternative matching as an acyclicity test
  report    classification of feasible alternatives (1a..1f, family 2)
  dsl       tiny abstract-MPI programs (the idiom twins, incl. metadata branches)
  explore   exhaustive explorer over DSL programs = ground truth
  cli       command line
"""
__version__ = "0.1.0"
