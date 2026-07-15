#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提供 Cloudflare 临时邮箱接口的命令行诊断工具。"""

import argparse
import time

from curl_cffi import requests
from mail_service import CloudflareMailClient, extract_verification_code


def create_address(api_base, auth_mode="none", api_key="", create_path="/api/new_address",
                   domain="", name="", timeout=20):
    import mail_service as _mail_service
    client = CloudflareMailClient(
        api_base, auth_mode=auth_mode, api_key=api_key,
        create_path=create_path, timeout=timeout,
    )
    original_requests = _mail_service.requests
    _mail_service.requests = requests
    try:
        return client.create_address(domain=domain, name=name)
    finally:
        _mail_service.requests = original_requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--address", default="")
    parser.add_argument("--credential", default="")
    parser.add_argument("--auth-mode", default="none", choices=["none", "bearer", "x-api-key", "x-admin-auth"])
    parser.add_argument("--api-key", default="")
    parser.add_argument("--create-path", default="/api/new_address")
    parser.add_argument("--domain", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--interval", type=int, default=3)
    args = parser.parse_args()

    client = CloudflareMailClient(
        args.api_base,
        auth_mode=args.auth_mode,
        api_key=args.api_key,
        create_path=args.create_path,
    )
    address = args.address.strip()
    credential = args.credential.strip()
    if not credential:
        address, credential = create_address(
            args.api_base,
            auth_mode=args.auth_mode,
            api_key=args.api_key,
            create_path=args.create_path,
            domain=args.domain,
            name=args.name,
        )
        print("[NEW] address=%s" % address)
        print("[NEW] credential(jwt)=%s" % credential)
    else:
        print("[USE] address=%s" % (address or "(unknown, from credential)"))

    deadline = time.time() + max(args.timeout, 1)
    seen_ids = set()
    while time.time() < deadline:
        boxes = client.probe_all_boxes(credential)
        total = 0
        for box_name, mails in boxes:
            if mails:
                print("[BOX] %s -> %s" % (box_name, len(mails)))
            total += len(mails)
            for item in mails:
                mail_id = item.get("id") or item.get("mail_id")
                if not mail_id or mail_id in seen_ids:
                    continue
                seen_ids.add(mail_id)
                detail = client.get_detail(credential, mail_id)
                subject, text = client.flatten_mail_text(item, detail)
                code = extract_verification_code(text, subject)
                print("[MAIL] id=%s subject=%r code=%r" % (mail_id, subject, code))
                if code:
                    print("[FOUND] %s" % code)
                    return
        if total == 0:
            print("[INFO] no mails yet")
        time.sleep(max(args.interval, 1))
    print("[TIMEOUT] no code found")


if __name__ == "__main__":
    main()
