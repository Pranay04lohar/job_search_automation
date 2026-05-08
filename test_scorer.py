"""Quick standalone test for the semantic scorer."""

import logging

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    from pipeline.scorer import SemanticMatcher
    from config import RESUME_TEXT

    matcher = SemanticMatcher(RESUME_TEXT)

    test_cases = [
        (
            "LLM Engineer role requiring Python, LangChain, RAG pipelines, FastAPI, and AWS Lambda experience",
            0.5,  # expected minimum
        ),
        (
            "Java backend developer for fintech payment systems with Spring Boot and Kubernetes",
            0.0,  # should be low
        ),
        (
            "AI/ML intern — NLP, transformers, Hugging Face, PyTorch, prompt engineering",
            0.45,
        ),
    ]

    print("\n=== Semantic Scorer Test ===")
    for text, min_expected in test_cases:
        score = matcher.score_text(text)
        status = "✅" if score >= min_expected else "⚠️ "
        print(f"{status} Score: {score:.3f} (min={min_expected}) | {text[:70]}...")

    print("\nAll tests done.")
