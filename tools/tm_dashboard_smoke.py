#!/usr/bin/env python3
"""TigerMemory Dashboard Smoke Tester.

Inputs: local dashboard endpoints and templates.
Outputs: diagnostics for page/API status and required data-contract fields.
"""

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser


class DashboardHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.body_page = None
        self.available_tabs = []
        self.sha_pill_content = None
        self.has_header = False
        self._in_sha_pill = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "body":
            self.body_page = attr_dict.get("data-page")
        elif tag == "header":
            self.has_header = True
        elif tag == "a" and "nav-tab" in attr_dict.get("class", ""):
            target = attr_dict.get("data-target-page")
            if target:
                self.available_tabs.append(target)
        elif tag == "code" and attr_dict.get("id") == "sha-pill":
            self._in_sha_pill = True

    def handle_endtag(self, tag):
        if tag == "code" and self._in_sha_pill:
            self._in_sha_pill = False

    def handle_data(self, data):
        if self._in_sha_pill:
            self.sha_pill_content = data.strip()


def check_sha_format(sha):
    if not sha:
        return False
    return bool(re.match(r"^[0-9a-fA-F]{7,40}$|^-+$", sha))


def _safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        safe_text = str(text).replace("\u2705", "[OK]").replace("\u274c", "[FAIL]")
        output_encoding = sys.stdout.encoding or "utf-8"
        print(safe_text.encode(output_encoding, errors="replace").decode(output_encoding))


def _has_field(payload, path):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _safe_request_json(opener, url, headers):
    start = time.time()
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                return {
                    "ok": False,
                    "status_code": response.status,
                    "latency_ms": round((time.time() - start) * 1000, 1),
                    "error": "response json is not object",
                }, None
            return {
                "ok": True,
                "status_code": response.status,
                "latency_ms": round((time.time() - start) * 1000, 1),
                "data": payload,
            }, payload
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error": str(exc),
        }, None


def _check_health_summary_payload(payload, checks):
    required = [
        "ok", "source", "source_path", "source_updated_at", "generated_at",
        "latency_ms", "cache", "cache.hit", "cache.ttl_seconds", "stale", "services",
        "repo_dirty"
    ]
    for field in required:
        checks[field] = _has_field(payload, field)
    checks["dashboard_version_present"] = _has_field(payload, "dashboard.version") and _has_field(payload, "dashboard.git_sha")
    checks["cache_source_present"] = _has_field(payload, "cache.source") if isinstance(payload.get("cache"), dict) else False
    checks["services_is_list"] = isinstance(payload.get("services"), list)


def _check_quality_payload(payload, checks):
    required = [
        "ok", "source", "source_path", "source_updated_at", "generated_at", "latency_ms",
        "cache", "cache.hit", "cache.ttl_seconds", "stale", "counts", "fallback",
        "repo_dirty",
    ]
    for field in required:
        checks[field] = _has_field(payload, field)
    checks["trace_summary_present"] = _has_field(payload, "trace_summary")
    checks["fallback_mode_present"] = "fallback_mode" in payload


def _check_digest_payload(payload, checks):
    checks["top_level_ok"] = payload.get("ok") is True
    checks["digest_present"] = _has_field(payload, "digest")
    if not checks["digest_present"]:
        return
    digest = payload["digest"]
    required = [
        "source", "source_path", "source_updated_at", "generated_at",
        "latency_ms", "fallback", "cached", "stale", "warnings", "errors",
        "cache", "cache.hit", "cache.ttl_seconds", "counts",
    ]
    for field in required:
        checks[f"digest.{field}"] = _has_field(digest, field)


def _check_canvas_payload(payload, checks):
    required = [
        "ok", "source", "source_path", "source_updated_at", "generated_at",
        "latency_ms", "cache", "cache.hit", "cache.ttl_seconds", "stale",
        "mermaid_src", "active_modules", "repo_dirty",
    ]
    for field in required:
        checks[field] = _has_field(payload, field)
    checks["active_modules_is_list"] = isinstance(payload.get("active_modules"), list)
    checks["mermaid_non_empty"] = bool(str(payload.get("mermaid_src") or "").strip())


def run_http_smoke(base_url, token=None):
    results = {}

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    headers = {"User-Agent": "TigerMemory-Smoke-Tester/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # healthz
    healthz_url = f"{base_url.rstrip('/')}/healthz"
    healthz_sha = None
    try:
        req = urllib.request.Request(healthz_url, headers=headers)
        with opener.open(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            healthz_sha = data.get("git_sha")
            results["healthz"] = {
                "ok": True,
                "status_code": response.status,
                "data": data,
            }
    except Exception as exc:
        results["healthz"] = {
            "ok": False,
            "error": f"Failed to fetch healthz: {str(exc)}",
        }

    # HTML pages
    pages = [
        ("/digest", "daily"),
        ("/health", "health"),
        ("/quality", "quality"),
        ("/agent-tools", "agent-tools"),
        ("/settings", "settings"),
        ("/canvas", "canvas"),
        ("/self-evolution", "self-evolution"),
    ]

    for path, expected_page in pages:
        page_url = f"{base_url.rstrip('/')}{path}"
        start_time = time.time()
        page_results = {
            "ok": False,
            "path": path,
            "status_code": None,
            "latency_ms": 0,
            "checks": {},
        }

        try:
            req = urllib.request.Request(page_url, headers=headers)
            with opener.open(req, timeout=5) as response:
                page_results["status_code"] = response.status
                content = response.read().decode("utf-8")
                page_results["latency_ms"] = round((time.time() - start_time) * 1000, 1)

                placeholders = [
                    "__HEADER__",
                    "__STYLE__",
                    "__DIGEST_JSON__",
                    "__CANVAS_JSON__",
                    "__HEALTH_JSON__",
                    "__QUALITY_JSON__",
                    "__SETTINGS_JSON__",
                    "__SELF_EVOLUTION_JSON__",
                ]
                leaks = [placeholder for placeholder in placeholders if placeholder in content]
                page_results["checks"]["placeholder_leak_free"] = len(leaks) == 0
                if leaks:
                    page_results["checks"]["leaks"] = leaks

                parser = DashboardHTMLParser()
                parser.feed(content)
                page_results["checks"]["has_header"] = parser.has_header
                page_results["checks"]["body_data_page_match"] = (parser.body_page == expected_page)
                page_results["checks"]["body_page"] = parser.body_page
                page_results["checks"]["nav_tab_exists"] = (expected_page in parser.available_tabs)
                page_results["checks"]["available_tabs"] = parser.available_tabs

                sha_found = parser.sha_pill_content
                page_results["checks"]["sha_pill_present"] = sha_found is not None
                page_results["checks"]["sha_pill"] = sha_found
                if sha_found:
                    is_valid_format = check_sha_format(sha_found)
                    page_results["checks"]["sha_pill_format_valid"] = is_valid_format
                    if healthz_sha:
                        page_results["checks"]["sha_pill_matches_healthz"] = (sha_found[:7] == healthz_sha[:7])
                    else:
                        page_results["checks"]["sha_pill_matches_healthz"] = False
                else:
                    page_results["checks"]["sha_pill_format_valid"] = False
                    page_results["checks"]["sha_pill_matches_healthz"] = False

                required_checks = [
                    page_results["checks"]["placeholder_leak_free"],
                    page_results["checks"]["has_header"],
                    page_results["checks"]["body_data_page_match"],
                    page_results["checks"]["nav_tab_exists"],
                    page_results["checks"]["sha_pill_format_valid"],
                ]
                page_results["ok"] = all(required_checks)
        except Exception as exc:
            page_results["ok"] = False
            page_results["error"] = str(exc)
            page_results["latency_ms"] = round((time.time() - start_time) * 1000, 1)

        results[path] = page_results

    # JSON APIs
    today = dt.date.today().strftime("%Y-%m-%d")
    api_endpoints = [
        ("/api/health/summary", _check_health_summary_payload),
        ("/api/quality/memory", _check_quality_payload),
        (f"/api/digest/{today}", _check_digest_payload),
        ("/api/canvas", _check_canvas_payload),
    ]
    for path, checker in api_endpoints:
        api_url = f"{base_url.rstrip('/')}{path}"
        api_result, payload = _safe_request_json(opener, api_url, headers)
        if not api_result["ok"]:
            results[path] = {
                "ok": False,
                **api_result,
                "path": path,
                "checks": {},
                "data": None,
            }
            continue

        data = api_result["data"]
        checks = {"status_code_present": 200 <= api_result["status_code"] < 300}
        checker(data, checks)
        checks["ok_field_present"] = "ok" in data
        checks["ok_true"] = data.get("ok") is True

        failed = [key for key, val in checks.items() if val is False]
        results[path] = {
            "ok": (len(failed) == 0),
            "path": path,
            "status_code": api_result["status_code"],
            "latency_ms": api_result["latency_ms"],
            "checks": checks,
            "failed_checks": failed,
            "data": data,
        }

    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="TigerMemory Dashboard Smoke Tester")
    parser.add_argument("--base-url", default="http://127.0.0.1:1998", help="Base URL to test")
    parser.add_argument("--token", default=os.getenv("TM_DASHBOARD_TOKEN"), help="Dashboard Auth Token")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")

    args = parser.parse_args(argv)

    start_time = time.time()
    http_results = run_http_smoke(args.base_url, args.token)
    total_duration = (time.time() - start_time) * 1000

    all_ok = all(
        result.get("ok", False)
        for key, result in http_results.items()
        if key != "healthz"
    )

    output = {
        "all_ok": all_ok,
        "browser_tested": False,
        "browser_reason": "Playwright module is not installed in the current environment.",
        "duration_ms": round(total_duration, 1),
        "results": http_results,
    }

    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        _safe_print("=" * 60)
        status_banner = "PASSED" if all_ok else "FAILED"
        _safe_print(f"TigerMemory Dashboard Smoke Test Status: {status_banner}")
        _safe_print(f"Base URL: {args.base_url}")
        _safe_print(f"Duration: {output['duration_ms']} ms")
        _safe_print(f"Browser integration: Disabled ({output['browser_reason']})")
        _safe_print("=" * 60)

        hz = http_results.get("healthz", {})
        if hz.get("ok"):
            data = hz.get("data", {})
            _safe_print(f"Backend Healthz: OK | Version: {data.get('version')} | SHA: {data.get('git_sha')}")
        else:
            _safe_print(f"Backend Healthz: FAIL | {hz.get('error')}")

        _safe_print("-" * 60)
        for path, result in http_results.items():
            if path == "healthz":
                continue
            status_text = "[OK]  " if result.get("ok") else "[FAIL]"
            latency_str = f"{result.get('latency_ms')}ms"
            _safe_print(f"{status_text} | {path:<18} | Latency: {latency_str:<8} | Code: {result.get('status_code')}")
            if not result.get("ok"):
                if result.get("error"):
                    _safe_print(f"  Error: {result.get('error')}")
                else:
                    checks = result.get("checks", {})
                    failed_checks = result.get("failed_checks") or [name for name, passed in checks.items() if not passed]
                    if failed_checks:
                        _safe_print(f"  Failed checks: {', '.join(failed_checks)}")
                    if checks.get("leaks"):
                        _safe_print(f"  Leaked Placeholders: {checks['leaks']}")
        _safe_print("=" * 60)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
