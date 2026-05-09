#!/usr/env python3
"""
tools/tm_llm.py — MiniMax-M2.7 LLM integration for expense auto-classification (P3).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

_AUTO_CLASSIFY_CONFIDENCE_THRESHOLD = 0.85


def _get_minimax_key() -> str | None:
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        return key
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            return json.load(f).get("env", {}).get("vars", {}).get("MINIMAX_API_KEY")
    except Exception:
        return None


def classify_expense(
    kind: str,
    amount: float,
    merchant: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    occurred_at: str | None = None,
    timeout: float = 30.0,
) -> dict:
    api_key = _get_minimax_key()
    if not api_key:
        return {"ok": False, "reason": "MINIMAX_API_KEY not configured"}

    parts = [f"Type: {kind}", f"Amount: {amount}"]
    if merchant:
        parts.append(f"Merchant: {merchant}")
    if note:
        parts.append(f"Note: {note}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    if occurred_at:
        parts.append(f"Date: {occurred_at}")

    try:
        import tm_expense
        result = tm_expense._read_categories()
        if result.get("ok"):
            category_list = [c["name"] for c in result.get("categories", []) if not c.get("archived")]
        else:
            category_list = ["餐饮", "交通", "购物", "娱乐", "居住", "医疗", "教育", "其他"]
    except Exception:
        category_list = ["餐饮", "交通", "购物", "娱乐", "居住", "医疗", "教育", "其他"]

    system_prompt = f"""Classify expense into one of these categories: {json.dumps(category_list, ensure_ascii=False)}.
Return JSON: {{"category": str, "confidence": float(0-1), "reasoning": str}}"""

    try:
        resp = httpx.post(
            "https://api.minimaxi.com/anthropic/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "MiniMax-M2.7", "max_tokens": 1024, "temperature": 0.1, "system": system_prompt, "messages": [{"role": "user", "content": "\n".join(parts)}]},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        return {"ok": False, "reason": "LLM request timeout"}
    except Exception as e:
        return {"ok": False, "reason": f"LLM request failed: {e}"}

    try:
        # Bug 2 fix: iterate content blocks to find type="text" (reasoning models put thinking first)
        text = ""
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                break
        if not text:
            return {"ok": False, "reason": "LLM response has no text block"}
        # Bug 3 fix: strip markdown code fence before JSON extraction
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```", "", text)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"ok": False, "reason": "LLM response missing JSON"}
        result = json.loads(m.group(0))
    except Exception as e:
        return {"ok": False, "reason": f"LLM response parse error: {e}"}

    category = result.get("category")
    if not category or category not in category_list:
        return {"ok": False, "reason": f"Invalid category: {category}"}

    try:
        confidence = float(result.get("confidence", 0))
        if not (0.0 <= confidence <= 1.0):
            return {"ok": False, "reason": f"Invalid confidence: {confidence}"}
        if confidence < 0.5:
            return {"ok": False, "reason": f"Confidence too low: {confidence}"}
    except (ValueError, TypeError):
        return {"ok": False, "reason": "Invalid confidence type"}

    return {"ok": True, "category": category, "confidence": confidence, "reasoning": result.get("reasoning", ""), "raw": resp.json()}
