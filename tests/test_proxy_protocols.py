import base64
import json
import unittest

from proxy_protocols import parse_proxy_line, parse_subscription_source


class ProxyProtocolParserTests(unittest.TestCase):
    def vmess_uri(self, **updates):
        value = {
            "v": "2",
            "ps": "VMess Test",
            "add": "vmess.example.com",
            "port": "443",
            "id": "11111111-1111-1111-1111-111111111111",
            "aid": "0",
            "scy": "auto",
            "net": "ws",
            "host": "cdn.example.com",
            "path": "/ws",
            "tls": "tls",
            "sni": "vmess.example.com",
            "fp": "chrome",
        }
        value.update(updates)
        payload = base64.b64encode(json.dumps(value).encode()).decode().rstrip("=")
        return "vmess://" + payload

    def test_supported_protocols_parse_to_expected_backends(self):
        lines = [
            "vless://11111111-1111-1111-1111-111111111111@vless.example.com:443?security=tls&sni=vless.example.com&type=ws&host=cdn.example.com&path=%2Fws#VLESS",
            "socks5://user:pass@socks.example.com:1080#SOCKS",
            "trojan://secret@trojan.example.com:443?sni=trojan.example.com&type=grpc&serviceName=test#Trojan",
            "hysteria2://secret@hy2.example.com:443?sni=hy2.example.com&obfs=salamander&obfs-password=obfs#HY2",
            self.vmess_uri(),
            "tuic://11111111-1111-1111-1111-111111111111:secret@tuic.example.com:443?sni=tuic.example.com&congestion_control=bbr&udp_relay_mode=native#TUIC",
        ]
        nodes = [parse_proxy_line(line) for line in lines]
        self.assertEqual([node.protocol for node in nodes], ["vless", "socks5", "trojan", "hysteria2", "vmess", "tuic"])
        self.assertEqual(nodes[1].backend, "native")
        for node in (nodes[0], nodes[2], nodes[3], nodes[4], nodes[5]):
            self.assertEqual(node.backend, "sing-box")
            self.assertTrue(node.outbound_config)

    def test_whole_subscription_base64_decodes_all_protocols(self):
        plain = "\n".join([
            "vless://11111111-1111-1111-1111-111111111111@a.example.com:443?security=tls&type=ws&path=%2Fws#A",
            "socks://user:pass@b.example.com:1080#B",
            "trojan://secret@c.example.com:443?sni=c.example.com#C",
            "hy2://secret@d.example.com:443?sni=d.example.com#D",
            self.vmess_uri(add="e.example.com"),
            "tuic://11111111-1111-1111-1111-111111111111:secret@f.example.com:443?sni=f.example.com#F",
        ])
        encoded = base64.urlsafe_b64encode(plain.encode()).decode().rstrip("=")
        result = parse_subscription_source(encoded)
        self.assertTrue(result.decoded_base64)
        self.assertEqual(len(result.nodes), 6)
        self.assertEqual(result.protocol_counts["socks5"], 1)
        self.assertEqual(result.protocol_counts["hysteria2"], 1)
        self.assertEqual(result.skipped, 0)

    def test_mixed_subscription_keeps_valid_nodes_and_reports_unsupported(self):
        text = "\n".join([
            "vless://11111111-1111-1111-1111-111111111111@ok.example.com:443?security=tls&type=ws&path=%2Fws#OK",
            "vless://11111111-1111-1111-1111-111111111111@bad.example.com:443?security=tls&type=xhttp#BAD",
            "unknown://value",
        ])
        result = parse_subscription_source(text)
        self.assertEqual(len(result.nodes), 1)
        self.assertEqual(result.skipped, 2)
        self.assertTrue(any("xhttp" in error for error in result.errors))
        self.assertTrue(any("unknown" in error for error in result.errors))

    def test_display_name_does_not_change_advanced_node_identity(self):
        first = parse_proxy_line("trojan://secret@a.example.com:443?sni=a.example.com#Name-A")
        second = parse_proxy_line("trojan://secret@a.example.com:443?sni=a.example.com#Name-B")
        self.assertEqual(first.node_id, second.node_id)
        self.assertNotEqual(first.name, second.name)

    def test_vless_reality_maps_tls_and_reality_options(self):
        node = parse_proxy_line(
            "vless://11111111-1111-1111-1111-111111111111@reality.example.com:443?"
            "security=reality&sni=www.example.com&fp=chrome&pbk=PUBLICKEY&sid=abcd&type=tcp#Reality"
        )
        tls = node.outbound_config["tls"]
        self.assertTrue(tls["enabled"])
        self.assertEqual(tls["server_name"], "www.example.com")
        self.assertEqual(tls["utls"]["fingerprint"], "chrome")
        self.assertEqual(tls["reality"]["public_key"], "PUBLICKEY")
        self.assertEqual(tls["reality"]["short_id"], "abcd")

    def test_common_aliases_and_edge_cases_are_normalized(self):
        vless = parse_proxy_line(
            "vless://11111111-1111-1111-1111-111111111111@a.example.com:443?"
            "security=tls&allow_insecure=1&packetEncoding=xudp&headerType=none"
        )
        self.assertTrue(vless.outbound_config["tls"]["insecure"])
        self.assertEqual(vless.outbound_config["packet_encoding"], "xudp")
        hy2 = parse_proxy_line(
            "hysteria2://secret@b.example.com:443?sni=b.example.com&obfs=none"
        )
        self.assertNotIn("obfs", hy2.outbound_config)

    def test_non_none_vless_and_vmess_header_types_are_rejected(self):
        with self.assertRaises(Exception):
            parse_proxy_line(
                "vless://11111111-1111-1111-1111-111111111111@a.example.com:443?headerType=http"
            )
        payload = {
            "v": "2", "ps": "bad", "add": "vmess.example.com", "port": "443",
            "id": "11111111-1111-1111-1111-111111111111", "aid": "0",
            "net": "tcp", "type": "http", "tls": ""
        }
        encoded = base64.b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        with self.assertRaises(Exception):
            parse_proxy_line("vmess://" + encoded)


if __name__ == "__main__":
    unittest.main()
