import os

# Pricing metadata is bundled with LiteLLM. Loading the application must not
# require a connection to GitHub, including in a fully local installation.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
