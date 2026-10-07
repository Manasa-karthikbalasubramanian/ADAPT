from app.intelligence import llm_provider


def test_provider_detection_defaults():
    # With no env vars, we should fall back to template
    assert llm_provider.active_provider() in {"template", "openrouter",
                                              "openai", "ollama"}


def test_status_shape():
    s = llm_provider.status()
    for k in ("active_provider", "available", "openrouter_key_set",
              "openrouter_base", "openrouter_model"):
        assert k in s
    assert s["openrouter_base"] == "https://openrouter.ai/api/v1"


def test_openrouter_model_default_is_free():
    import os
    model = os.getenv("OPENROUTER_MODEL",
                      llm_provider.DEFAULT_OPENROUTER_MODEL)
    assert isinstance(model, str) and len(model) > 0
