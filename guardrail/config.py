"""The one place to change which Claude models the project uses, and how."""

# Policy Compiler (milestone 2): accuracy matters most, and it runs rarely.
MODEL = "claude-opus-5-5"
EFFORT = "medium"        # low | medium | high | xhigh | max
MAX_TOKENS = 16000       # room for the model's thinking plus a short JSON answer
TIMEOUT_SECONDS = 60     # per attempt; a slow call is treated as a failure
MAX_RETRIES = 2          # the SDK retries rate limits and server errors this many times

# Sample treasury agent (milestone 3): runs hundreds of times in Crash Lab, so a
# faster, cheaper model. Sonnet is a realistic choice for a company's real agent,
# so a fooled agent can't be waved away as "you picked a weak model".
AGENT_MODEL = "claude-sonnet-5-5"
AGENT_EFFORT = "medium"
AGENT_MAX_TOKENS = 16000
AGENT_MAX_STEPS = 30     # model calls per run; hitting it ends the run

# Unusual payment check, rule F1 (milestone 4): a small yes/no judgment on about
# ten facts, run on every payment the hard rules allow, so the cheapest, fastest
# model. A wrong answer is bounded: it can only send a payment to a human, or
# miss a flag while every hard rule still applies. Haiku 4.5 takes no effort setting.
UNUSUAL_MODEL = "claude-haiku-4-5"
UNUSUAL_MAX_TOKENS = 1024        # the answer is two short fields
UNUSUAL_TIMEOUT_SECONDS = 15     # a slow call is a failure, and failures escalate
UNUSUAL_MAX_RETRIES = 1

# Crash Lab (milestone 5).
CRASH_LAB_REPEATS = 3        # Layer 2 runs each scenario this many times per mode (AI isn't consistent)
CRASH_LAB_WORKERS = 8        # agent runs in parallel; the SDK retries if we hit a rate limit

# Suggested rule fixes for unsafe results: rare and accuracy matters, so the compiler's model.
FIX_MODEL = MODEL
FIX_EFFORT = "medium"
FIX_MAX_TOKENS = 16000

# Prices in US dollars per million tokens, used only for the cost estimate and the
# cost report. Checked against Anthropic's published prices on 2026-10-04.
# Cache writes (5-minute cache) cost 1.25x input.
PRICES = {
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
}
