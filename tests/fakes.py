import re

from app.llm.base import LLMError, LLMResult


class FakeProvider:
    """Scores posts by keyword so tests are deterministic. Records every prompt."""

    def __init__(self, name="anthropic", model="fake-model", keyword="bug", fail=False, drop_ids=()):
        self.name, self.model = name, model
        self.keyword, self.fail, self.drop_ids = keyword, fail, set(drop_ids)
        self.overloaded = False
        self.prompts: list[str] = []

    async def complete_json(self, system, user, schema, max_tokens=8000):
        self.prompts.append(user)
        if self.fail:
            raise LLMError("boom")
        if self.overloaded:
            raise LLMError("503 high demand", retryable=True, retry_after=None)
        if "wants" in schema["properties"]:
            return LLMResult({"wants": ["bug fixes"], "avoid": ["- showcases"], "changes": "added bugs"},
                             self.model, 100, 20)
        results = []
        for post_id, body in re.findall(r'<post id="(\d+)"[^>]*>(.*?)</post>', user, re.DOTALL):
            if int(post_id) in self.drop_ids:
                continue
            hit = self.keyword in body.lower()
            results.append(
                {
                    "id": int(post_id),
                    "relevance": 150 if hit else 10,  # out of range on purpose: must be clamped
                    "matched_interests": [1, 999] if hit else [],
                    "reason": "keyword" if hit else "no keyword",
                    "summary": "sum",
                }
            )
        return LLMResult({"results": results}, self.model, 1000, 200)
