"""Risk and capital management (REQ-007).

Strategies decide *direction*; this package decides *size* and *leverage*, enforces
per-strategy capital and loss limits, and escalates the leverage multiplier when a
position would otherwise be below the exchange minimum order size.
"""
