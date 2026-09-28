import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


def decrypt_ts(data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(data)
    # HLS 标准使用 PKCS7 填充；如果分片不是整块加密的，可以去掉 unpad
    try:
        return unpad(decrypted, AES.block_size)
    except ValueError:
        return decrypted


def load_file(path: str) -> bytes:
    """以二进制方式读取本地文件"""
    with open(path, "rb") as f:
        return f.read()


def write_file(data: bytes, path: str):
    with open(path, "wb") as f:
        f.write(data)


def request_m3u8(url: str, proxy=None) -> str:
    if proxy:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"}, proxies=proxy)
    else:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"})
    # response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"})
    response.raise_for_status()
    return response.text


if __name__ == '__main__':
    url = "https://jable.tv/videos/royd-352"
    proxy = {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890"
    }
    r = requests.get(url,
                     headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"},
                     proxies=proxy)
    print(r.text)