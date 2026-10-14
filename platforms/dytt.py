import re
import urllib.parse

import requests
import time

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36'


def fetch_html(url):
    max_retries = 5
    delay = 1  # 初始延迟（秒）
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers={'User-Agent': UA}, timeout=30)
            resp.raise_for_status()  # 若状态码非2xx则抛出HTTPError
            return resp.text
        except requests.exceptions.RequestException as e:
            # 如果最后一次尝试也失败，则抛出异常
            if attempt == max_retries - 1:
                raise
            # 等待一段时间再重试，采用指数退避（1s, 2s, 4s, 8s）
            time.sleep(delay * (2 ** attempt))


def get_initial_m3u8_url(input_url):
    html_text = fetch_html(input_url)
    pattern = r'https?:\\/\\/[^"\']+index\.m3u8'
    match = re.search(pattern, html_text)
    if match:
        cleaned = match.group(0).replace('\\/', '/')
        decoded = cleaned.encode('utf-8').decode('unicode_escape')
        encoded = urllib.parse.quote(decoded, safe=':/')
        return encoded
    else:
        raise ValueError("[!] 获取index url失败")


