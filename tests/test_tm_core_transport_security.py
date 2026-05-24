# -*- coding: utf-8 -*-
"""
tests/test_tm_core_transport_security.py — 针对 Network Transports Bearer Guard 传输安全阻断器的回归测试。
包含超 10 个精确测试用例，完全覆盖 IPv4 私有网段 (RFC 1918)、Tailscale 网段 (CGNAT)、边界溢出、合法性校验等情况。
"""

import os
import unittest
import pytest

# 导入待测核心函数
import tigermemory_core as tm_core


class TestTransportSecurity(unittest.TestCase):
    def setUp(self):
        # 每次测试前清理临时豁免环境变量
        if "TM_ALLOW_UNSECURE_HTTP" in os.environ:
            del os.environ["TM_ALLOW_UNSECURE_HTTP"]

    def tearDown(self):
        if "TM_ALLOW_UNSECURE_HTTP" in os.environ:
            del os.environ["TM_ALLOW_UNSECURE_HTTP"]

    def test_secure_https_passes(self):
        # HTTPS 加密通道必须无条件放行
        try:
            tm_core.check_transport_security("https://anything")
            tm_core.check_transport_security("https://api.openai.com:9000")
        except RuntimeError:
            self.fail("HTTPS scheme should pass without RuntimeError")

    def test_loopback_passes(self):
        # localhost, 127.x.x.x, [::1], ::1 等本地回环通道放行
        try:
            tm_core.check_transport_security("http://localhost:9000")
            tm_core.check_transport_security("http://127.0.0.1:9000")
            tm_core.check_transport_security("http://[::1]:9000")
        except RuntimeError:
            self.fail("Loopback hosts should pass without RuntimeError")

    def test_tailscale_cgnat_passes(self):
        # Tailscale CGNAT 专网 IP 范围是 100.64.0.0 到 100.127.255.255
        try:
            tm_core.check_transport_security("http://100.65.0.1:9000")
            tm_core.check_transport_security("http://100.64.0.0:9000")
            tm_core.check_transport_security("http://100.127.255.255:9000")
        except RuntimeError:
            self.fail("Valid Tailscale range IPs should pass without RuntimeError")

    def test_rfc1918_private_blocks_passes(self):
        # RFC 1918 私有局域网网段 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
        try:
            tm_core.check_transport_security("http://192.168.1.100:9000")  # /16
            tm_core.check_transport_security("http://10.0.0.5:9000")       # /8
            tm_core.check_transport_security("http://172.16.0.1:9000")      # /12 下限
            tm_core.check_transport_security("http://172.31.255.255:9000")  # /12 上限
        except RuntimeError:
            self.fail("Valid RFC 1918 private IPs should pass without RuntimeError")

    def test_invalid_and_public_rejects(self):
        # 1. 错误的 IP 分片边界 (Bug 1：100.65.999.5 曾经被 Tailscale 放行)
        with self.assertRaises(RuntimeError):
            tm_core.check_transport_security("http://100.65.999.5:9000")

        # 2. 刚好超出 RFC 1918 的 172.16.0.0/12 范围
        with self.assertRaises(RuntimeError):
            tm_core.check_transport_security("http://172.32.0.1:9000")

        # 3. 公网 IP 阻断
        with self.assertRaises(RuntimeError):
            tm_core.check_transport_security("http://8.8.8.8:9000")

        # 4. 公网域名阻断
        with self.assertRaises(RuntimeError):
            tm_core.check_transport_security("http://api.openai.com:9000")

        # 5. 伪装回环域名绕过防御 (Bug 2: 127.0.0.1.evil.test 曾经被 127. 前缀过滤放行)
        with self.assertRaises(RuntimeError):
            tm_core.check_transport_security("http://127.0.0.1.evil.test:8000")

    def test_env_exempt_passes_all(self):
        # 当配置了 TM_ALLOW_UNSECURE_HTTP=1 环境变量时，所有请求必须无条件放行
        os.environ["TM_ALLOW_UNSECURE_HTTP"] = "1"
        try:
            tm_core.check_transport_security("http://100.65.999.5:9000")
            tm_core.check_transport_security("http://172.32.0.1:9000")
            tm_core.check_transport_security("http://8.8.8.8:9000")
            tm_core.check_transport_security("http://api.openai.com:9000")
        except RuntimeError:
            self.fail("TM_ALLOW_UNSECURE_HTTP=1 should exempt all unsecure HTTP requests")
