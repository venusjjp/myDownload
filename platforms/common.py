import os
import shutil
import subprocess
import time
from urllib.parse import urljoin

import m3u8
import requests
from Crypto.Cipher import AES


def get_final_m3u8_url(m3u8_url):
    """
    m3u8_url: 任一阶段的 m3u8 地址
    fetch_func: 你已有的抓取函数，签名 fetch_func(url, proxy=proxy) -> str
    返回：目标 media m3u8 的绝对 URL
    """

    playlist = m3u8.load(m3u8_url)
    if playlist.is_variant:
        base_url = playlist._base_uri
        uri = playlist.playlists[0].uri
        abs_uri = urljoin(base_url, uri)
        return get_final_m3u8_url(abs_uri)
    else:
        return m3u8_url


def parse_media_info(playlist):
    # playlist = m3u8.load(final_m3u8_url)
    dic = {"ts_list": [urljoin(playlist._base_uri, ts.uri) for ts in playlist.segments]}
    if len(playlist.keys) >= 1:
        if len(playlist.keys) > 1:
            print(f'[!] 发现多个keys情况')
        key = playlist.keys[0]
        if key:
            dic['encrypted'] = True
            dic['method'] = key.method
            dic['key_url'] = urljoin(playlist._base_uri, key.uri)
            dic['iv'] = bytes.fromhex(key.iv[2:])   # 注意：iv 是字符串形式，如 "0x..."
        else:
            dic['encrypted'] = False
    else:
        dic['encrypted'] = False
    return dic


def decrypt_ts_AES128(cipher_bytes, key_bytes: bytes, iv: bytes):

    # key_bytes必须是bytes类型，长度为 16
    # IV 也必须是 bytes 类型，且长度必须为 16
    # 这里使用全 0 的 IV (标准默认值)
    # 此时 key 是 16 字节，iv 是 16 字节，符合 AES-128-CBC 标准
    cryptor = AES.new(key_bytes, AES.MODE_CBC, iv)
    plain_bytes = cryptor.decrypt(cipher_bytes)
    return plain_bytes


def fetch_html(url, headers, proxy):
    max_retries = 5
    delay = 1  # 初始延迟（秒）
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=30, proxies=proxy)
            resp.raise_for_status()  # 若状态码非2xx则抛出HTTPError
            return resp
        except requests.exceptions.RequestException as e:
            # 如果最后一次尝试也失败，则抛出异常
            if attempt == max_retries - 1:
                raise
            # 等待一段时间再重试，采用指数退避（1s, 2s, 4s, 8s）
            time.sleep(delay * (2 ** attempt))


def fetch_key(key_url, headers, proxy):
    resp = fetch_html(key_url, headers, proxy)
    return resp.content


def remove_advertisement(ts_list):
    """根据路径特征删除广告片段"""
    templates = [
        "/video/adjump/",       # dytt广告片段特征
    ]
    _lis = ts_list.copy()
    for ts_url in ts_list:
        for t in templates:
            if t in ts_url:
                _lis.remove(ts_url)
    if len(_lis) < len(ts_list):
        print(f"[*] 含有广告片段，已删除")
    return _lis


def check_ffmpeg():

    """
    检查 ffmpeg 是否可用，返回可用路径。
    检查顺序：
      1. 系统 PATH
      2. 当前工作目录下的 ./tools/ffmpeg.exe
    找不到则抛 FileNotFoundError。
    """
    # 1) 系统 PATH
    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    # 2) 当前工作目录 ./tools/ffmpeg.exe
    local_exe = os.path.join(os.getcwd(), "tools", "ffmpeg.exe")
    if os.path.isfile(local_exe):
        return local_exe

    raise FileNotFoundError(
        "未找到 ffmpeg！请任选一种方式解决：\n"
        "  1. 安装 ffmpeg 并加入系统 PATH；\n"
        "  2. 将 ffmpeg.exe 放到当前工作目录的 ./tools/ 下。"
    )


def download_ts(ts_url, save_path, key_bytes=None, iv=None, retries=10):
    """
    下载单个 .ts 文件，支持重试
    此方法不用proxy方式，是因为proxy方式是为了省流量，如果下载地址需代理，则不如直接开全局代理
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"
    }
    if os.path.exists(save_path):
        return True
    for attempt in range(retries):
        try:
            resp = requests.get(ts_url, headers=headers, timeout=30)
            resp.raise_for_status()
            if key_bytes:
                ts_bytes = decrypt_ts_AES128(resp.content, key_bytes, iv)
            else:
                ts_bytes = resp.content
            with open(save_path, "wb") as f:
                f.write(ts_bytes)
            return True
        except Exception as e:
            # print(f"  [!] 尝试 {attempt+1}/{retries} 失败: {ts_url} - {e}\n{traceback.format_exc()}")
            print(f"  [!] 尝试 {attempt+1}/{retries} 失败: {ts_url} - {e}")
    return False


def merge_with_ffmpeg(ts_dir, output_file, ffmpeg_path):
    """使用 ffmpeg concat 协议合并 TS 片段"""
    ts_files = sorted([f for f in os.listdir(ts_dir) if f.endswith(".ts")])
    if not ts_files:
        print("[!] 没有 .ts 文件可以合并")
        return

    file_list_path = os.path.join(ts_dir, "filelist.txt")
    with open(file_list_path, "w", encoding="utf-8") as f:
        for ts_file in ts_files:
            f.write(f"file '{ts_file}'\n")

    abs_output_path = os.path.join(os.path.dirname(os.path.abspath(ts_dir)), output_file)
    print(f"\n[+] 正在使用 ffmpeg 合并 {len(ts_files)} 个片段... -> {abs_output_path}")
    cmd = [
        ffmpeg_path, "-f", "concat", "-safe", "0", "-i", "filelist.txt",
        "-c", "copy", abs_output_path, "-y"
    ]
    try:
        # 在 ts_dir 目录下执行，这样 filelist.txt 和相对路径的 ts 文件都能被正确找到
        subprocess.run(cmd, check=True, cwd=ts_dir)
        print(f"[+] 合并完成，输出文件: {abs_output_path}")
    except subprocess.CalledProcessError as e:
        print(f"[!] ffmpeg 合并失败: {e}")
        raise


