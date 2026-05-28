# Offline: compute H(G) from collected word_results.
# Not yet implemented.
#
# Note on scale conversion (Kolmogorov comparison):
#   Kolmogorov's estimates (0.9–1.4 bits/char) are at the character level.
#   To compare: divide bits/word by mean word length in chars including spaces
#   (~5.5 for Russian prose). Result is approximate.
#
# Note on truncation bias:
#   Capping at G=6 underestimates true entropy (failures are bounded, not exact).
#   This yields a lower bound on the upper bound. Document in any published output.
