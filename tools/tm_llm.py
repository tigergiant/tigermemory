#!/usr/env python3
"""
tools/tm_llm.py — MiniMax-M2.7 LLM integration for expense auto-classification (P3).
Inputs: CLI arguments, local repository files, or data supplied by the caller.
Outputs: A deterministic stdout report, file rewrite, or helper return value documented by the command.
Depends-on (must-have): Python stdlib and local tigermemory helper modules; external services only when explicitly requested.
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


def classify_status(
    direction: str,
    amount: float,
    product: str,
    remark: str,
    trans_type: str,
    payment_method: str,
    source: str,
    timeout: float = 30.0,
) -> dict:
    """LLM fallback for transaction status classification.

    Returns {ok: bool, status: str, confidence: float, reasoning: str, raw: dict}
    status is one of: success, refunded, closed, internal_transfer
    """
    api_key = _get_minimax_key()
    if not api_key:
        return {"ok": False, "reasoning": "MINIMAX_API_KEY not configured"}

    system_prompt = (
        "你是个人记账系统的交易状态分类器。判断这条交易的 status，从以下 4 个值中选 1 个：\n"
        "\n"
        "- success：正常交易，钱真扣/真到账，计入收支统计\n"
        "- refunded：退款、退货、退还款，钱回流，不计入正常收入\n"
        "- closed：交易关闭、支付失败、金额为 0 的授权解冻，钱没真动\n"
        "- internal_transfer：自有账户间转账（信用卡还款、余额宝转入/转出、零钱通转入、转账给自己），钱在自己账户内移动，不计入收支\n"
        "\n"
        "输出严格 JSON：\n"
        '{"status": "<one_of_4>", "confidence": <0-1>, "reasoning": "<why>"}'
    )

    user_prompt = (
        f"交易信息：\n"
        f"- 交易类型：{trans_type}\n"
        f"- 收/支：{direction}\n"
        f"- 金额：¥{amount}\n"
        f"- 商品说明：{product}\n"
        f"- 备注：{remark}\n"
        f"- 支付方式：{payment_method}\n"
        f"- 来源渠道：{source}"
    )

    try:
        resp = httpx.post(
            "https://api.minimaxi.com/anthropic/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 1024,
                "temperature": 0.1,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        return {"ok": False, "reasoning": "LLM request timeout"}
    except Exception as e:
        return {"ok": False, "reasoning": f"LLM request failed: {e}"}

    try:
        text = ""
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                break
        if not text:
            return {"ok": False, "reasoning": "LLM response has no text block"}
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```", "", text)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"ok": False, "reasoning": "LLM response missing JSON"}
        result = json.loads(m.group(0))
    except Exception as e:
        return {"ok": False, "reasoning": f"LLM response parse error: {e}"}

    status = result.get("status")
    valid_statuses = {"success", "refunded", "closed", "internal_transfer"}
    if not status or status not in valid_statuses:
        return {"ok": False, "reasoning": f"Invalid status: {status}"}

    try:
        confidence = float(result.get("confidence", 0))
        if not (0.0 <= confidence <= 1.0):
            return {"ok": False, "reasoning": f"Invalid confidence: {confidence}"}
        if confidence < 0.7:
            return {"ok": False, "reasoning": f"Confidence too low: {confidence}"}
    except (ValueError, TypeError):
        return {"ok": False, "reasoning": "Invalid confidence type"}

    return {
        "ok": True,
        "status": status,
        "confidence": confidence,
        "reasoning": result.get("reasoning", ""),
        "raw": resp.json(),
    }
