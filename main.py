import os
import re
import shutil
import subprocess
import time
import traceback
import urllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES

# ---------- 配置 ----------
WORKSPACE = ".\\workspace"
MAX_WORKERS = 10                    # 并发下载线程数
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
}
PROXIES = {
    "http": "127.0.0.1:7890",
    "https": "127.0.0.1:7890",
}
NO_PROXIES = {
    "http": None,
    "https": None,
}
DEFAULT_IV = b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'


def fetch_html(url, proxy=False):
    max_retries = 5
    delay = 1  # 初始延迟（秒）
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, proxies = PROXIES if proxy else NO_PROXIES)
            resp.raise_for_status()  # 若状态码非2xx则抛出HTTPError
            return resp.text
        except requests.exceptions.RequestException as e:
            # 如果最后一次尝试也失败，则抛出异常
            if attempt == max_retries - 1:
                raise
            # 等待一段时间再重试，采用指数退避（1s, 2s, 4s, 8s）
            time.sleep(delay * (2 ** attempt))


def identify_m3u8_type(m3u8_content):
    if "#EXT-X-STREAM-INF" in m3u8_content:
        return "MASTER"
    elif "#EXTINF" in m3u8_content:
        return "MEDIA"
    elif "#EXT-X-TARGETDURATION" in m3u8_content:
        return "MEDIA"
    return None


def get_child_m3u8_url(m3u8_content, m3u8_url):
    """从 master m3u8 中取出子 playlist 的绝对 URL"""
    lines = m3u8_content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("#EXT-X-STREAM-INF"):
            # 下一行非注释行就是子 playlist URL
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if nxt and not nxt.startswith("#"):
                    if 'http' not in nxt:
                        nxt = urljoin(m3u8_url, nxt)
                    return nxt
    return None


def get_ts_m3u8_url(m3u8_url, fetch_func, proxy=None):
    """
    m3u8_url: 起始 m3u8 地址
    fetch_func: 你已有的抓取函数，签名 fetch_func(url, proxy=proxy) -> str
    返回：目标 media m3u8 的绝对 URL
    """
    content = fetch_func(m3u8_url, proxy=proxy)
    kind = identify_m3u8_type(content)

    if kind == "MASTER":
        _url = get_child_m3u8_url(content, m3u8_url)
        if not _url:
            raise ValueError("MASTER 中未找到子 playlist 地址")
        # 递归直到拿到 MEDIA
        return get_ts_m3u8_url(_url, fetch_func, proxy=proxy)

    elif kind == "MEDIA":
        return m3u8_url

    else:
        raise ValueError("未知的 M3U8 文件类型")


def get_ts_list(target_content, target_url):
    ts_list = []
    lines = target_content.splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(".ts"):
            if 'http' not in line:
                line = urljoin(target_url, line)
            ts_list.append(line)
    return ts_list


def remove_advertisement(ts_list):
    """根据路径特征删除广告片段"""
    _lis = ts_list.copy()
    for ts_url in ts_list:
        if "/video/adjump/" in ts_url:
            _lis.remove(ts_url)
    if len(_lis) < len(ts_list):
        print(f"含有广告片段，已删除")
    return _lis


def is_dytt(input_url):
    return input_url.startswith("https://www.dytt001.com/")


def extract_dytt(html_text):
    pattern = r'https?:\\/\\/[^"\']+index\.m3u8'
    match = re.search(pattern, html_text)
    if match:
        cleaned = match.group(0).replace('\\/', '/')
        decoded = cleaned.encode('utf-8').decode('unicode_escape')
        encoded = urllib.parse.quote(decoded, safe=':/')
        return encoded
    else:
        raise ValueError("获取index url失败")


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


def fetch_key(key_url, proxy=True):
    """
    尝试下载 M3U8 视频流的密钥文件。

    参数:
        key_url (str): 密钥文件的完整 URL 地址。

    返回:
        bytes or None:
            - 如果下载成功，返回密钥的二进制内容 (bytes)。
            - 如果 URL 无效、文件不存在或网络请求失败，返回 None。
    """
    try:
        if not key_url or not key_url.startswith('http'):
            print(f"❌ 无效的 Key URL: {key_url}")
            raise ValueError("无效的 Key URL")
        response = requests.get(key_url, headers=HEADERS, timeout=30, proxies=PROXIES if proxy else NO_PROXIES)
        if response.status_code == 200:
            # 4. 返回二进制内容
            # 注意：Key 必须是 bytes 类型才能用于 AES 解密
            # print(f"✅ 成功获取密钥，长度: {len(response.content)} 字节")
            return response.content
        elif response.status_code in [403, 404]:
            return None
        else:
            print(f"⚠️ 获取 Key 失败，HTTP 状态码: {response.status_code}")
            raise ValueError("请求 Key 时发生错误")

    except requests.exceptions.RequestException as e:
        # 捕获所有网络相关的异常 (如连接超时、DNS错误、404等)
        print(f"⚠️ 请求 Key 时发生网络错误: {e}")
        raise e

    except Exception as e:
        # 捕获其他意外错误
        print(f"❌ 发生未知错误: {e}")
        raise e


def parse_m3u8_encryption(m3u8_content: str) -> Dict:
    """
    解析 m3u8 内容，判断是否加密，并提取 key 地址和 IV。

    :param m3u8_content: m3u8 文件的文本内容
    :return: 一个字典，包含以下字段：
        - encrypted (bool): 是否加密
        - method (str): 加密方法，如 'AES-128'、'SAMPLE-AES'、'NONE'
        - key_uri (str | None): key 的地址
        - iv (str | None): IV 的值
        - keys (List[Dict]): 所有出现的 #EXT-X-KEY 标签信息（可能分段使用不同的 key）
    """
    result = {
        "encrypted": False,
        "method": None,
        "key_uri": None,
        "iv": None,
        "keys": [],
    }

    if "#EXT-X-KEY" not in m3u8_content:
        return result
    # 匹配 #EXT-X-KEY 行
    key_pattern = re.compile(r'^#EXT-X-KEY:(.+)$', re.MULTILINE)

    for match in key_pattern.finditer(m3u8_content):
        attr_str = match.group(1)

        # 解析属性，如 METHOD=AES-128,URI="...",IV=0x...
        # 注意：URI 的值可能带引号，且可能包含逗号
        attrs = {}
        # 用正则匹配 KEY=VALUE 或 KEY="VALUE"
        attr_pattern = re.compile(r'([A-Z0-9\-]+)=("[^"]*"|[^,]*)')
        for attr_match in attr_pattern.finditer(attr_str):
            key = attr_match.group(1).strip()
            value = attr_match.group(2).strip().strip('"')
            attrs[key] = value
        key_info = {
            "method": attrs.get("METHOD"),
            "uri": attrs.get("URI"),
            "iv": bytes.fromhex(attrs.get("IV")[2:])
        }
        result["keys"].append(key_info)

    # 判断是否加密：只要存在一个 METHOD 不是 NONE 的 key，就认为加密
    for key_info in result["keys"]:
        method = key_info.get("method")
        if method and method.upper() != "NONE":
            result["encrypted"] = True
            result["method"] = method
            result["key_uri"] = key_info.get("uri")
            result["iv"] = key_info.get("iv")
            break
    if len(result["keys"]) > 1:
        raise ValueError(f"Multiple keys found in the m3u8 file:\n{m3u8_content}")
    if result['method'] != 'AES-128':
        raise ValueError(f"Unsupported encryption method: {result['method']}\n{m3u8_content}")

    return result


def decrypt_ts_AES128(cipher_bytes, key_bytes: bytes, iv: bytes):

    # key_bytes必须是bytes类型，长度为 16
    # IV 也必须是 bytes 类型，且长度必须为 16
    # 这里使用全 0 的 IV (标准默认值)
    # 此时 key 是 16 字节，iv 是 16 字节，符合 AES-128-CBC 标准
    cryptor = AES.new(key_bytes, AES.MODE_CBC, iv)
    plain_bytes = cryptor.decrypt(cipher_bytes)
    return plain_bytes


def download_ts(ts_url, save_path, key_bytes=None, iv=None, retries=10):
    if os.path.exists(save_path):
        return True
    """下载单个 .ts 文件，支持重试"""
    for attempt in range(retries):
        try:
            resp = requests.get(ts_url, headers=HEADERS, timeout=30, proxies=NO_PROXIES)
            resp.raise_for_status()
            if key_bytes:
                ts_bytes = decrypt_ts_AES128(resp.content, key_bytes, iv)
            else:
                ts_bytes = resp.content
            with open(save_path, "wb") as f:
                f.write(ts_bytes)
            return True
        except Exception as e:
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


def download_and_merge(
        output_file,
        input_url=None,          # 必备参数，即使提供本地m3u8，也需要从这个参数中获取base_url
        proxy=False,
        ts_m3u8_url=None,       # 用于m3u8文件无法通过requests爬取时, 提供base_url
        ts_m3u8_path=None,      # 用于m3u8文件无法通过requests爬取时, 从本地文件读取m3u8内容
        key_path=None,          # 用于加密key无法通过requests爬取时
        iv=None,                # iv如不提供，则使用默认值
):

    # 参数检查
    if input_url and ts_m3u8_url:
        raise ValueError("不能同时提供 input_url 和 ts_m3u8_url")
    if ts_m3u8_url and not ts_m3u8_path or not ts_m3u8_url and ts_m3u8_path:
        raise ValueError("ts_m3u8_url 和 ts_m3u8_path 必须同时提供")

    # 预处理
    os.makedirs(WORKSPACE, exist_ok=True)
    ffmpeg_path = check_ffmpeg()
    key_bytes = None

    try:
        print(f"[*] 正在获取视频信息 ->《{output_file.removesuffix('.mp4')}》...")
        failure_list = []

        # 1. 获取 ts m3u8 内容
        if input_url:
            if is_dytt(input_url):
                html_text = fetch_html(input_url, proxy=proxy)
                initial_m3u8_url = extract_dytt(html_text)
                print(f"[*] 获取到dytt index url: {initial_m3u8_url}")
            else:
                initial_m3u8_url = input_url
            ts_m3u8_url = get_ts_m3u8_url(initial_m3u8_url, fetch_html, proxy=proxy)
            print(f"[*] 获取到ts m3u8 url: {ts_m3u8_url}")
            ts_m3u8_content = fetch_html(ts_m3u8_url, proxy=proxy)
        elif ts_m3u8_path:
            with open(ts_m3u8_path, 'r') as f:
                ts_m3u8_content = f.read()
        else:
            raise ValueError("必须提供 input_url 或 ts_m3u8_url")

        if not ts_m3u8_content:
            raise RuntimeError("无法获取 ts m3u8 内容")

        # 2. 处理加密
        encryption_info = parse_m3u8_encryption(ts_m3u8_content)
        if encryption_info["encrypted"]:
            if key_path:
                with open(key_path, 'rb') as f:
                    key_bytes = f.read()
                if not iv:
                    iv = DEFAULT_IV
            else:
                key_uri = encryption_info["key_uri"]
                if not key_uri.startswith("http"):
                    key_uri = urljoin(ts_m3u8_url, key_uri)
                key_bytes = fetch_key(key_uri, proxy=proxy)
                iv = encryption_info["iv"]
            print(f'[*]该视频已加密，key: {key_bytes}, iv: {iv}')
        else:
            print('[*]该视频未加密')

        # 3. 获取ts下载列表
        ts_list = get_ts_list(ts_m3u8_content, ts_m3u8_url)
        if input_url and is_dytt(input_url):
            ts_list = remove_advertisement(ts_list)
            print("[*] 已移除广告片段")

        # 创建临时目录
        temp_dir = os.path.join(WORKSPACE, f'tmp_({output_file.removesuffix(".mp4")})')
        os.makedirs(temp_dir, exist_ok=True)

        # 4. 准备下载任务列表
        download_tasks = []
        for idx, url in enumerate(ts_list):
            # 使用序号作为文件名，确保顺序正确
            filename = f"{idx:06d}.ts"
            save_path = os.path.join(temp_dir, filename)
            download_tasks.append((url, save_path, key_bytes, iv, idx))

        # 5. 多线程下载
        print("[*] 开始下载片段（多线程）...")
        success_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_task = {
                executor.submit(download_ts, url, path, key_bytes, iv): (url, path, key_bytes, iv, idx)
                for url, path, key_bytes, iv, idx in download_tasks
            }

            for future in as_completed(future_to_task):
                url, path, key_bytes, iv, idx = future_to_task[future]
                try:
                    ok = future.result()
                    if ok:
                        success_count += 1
                        if success_count % 50 == 0:
                            print(f"  进度: {success_count}/{len(ts_list)}")
                    else:
                        failure_list.append((path, url))
                        print(f"  [×] 下载失败: {url}")
                except Exception as e:
                    print(f"  [×] 异常: {url} - {e}")

        print(f"[*] 下载完成: 成功 {success_count} / 总数 {len(ts_list)}")

        if success_count == 0:
            print("[!] 没有下载到任何片段，退出。")
            return
        elif success_count < len(ts_list):
            print("[!] 以下片段下载失败：")
            for path, url in failure_list:
                print(f"  [×] 失败: {path} -> {url}")
            return

        # 6. 使用 ffmpeg 合并为 MP4
        merge_with_ffmpeg(temp_dir, output_file, ffmpeg_path=ffmpeg_path)

        # 7. （可选）清理临时文件
        print("[*]合并完成，开始清除临时文件...")
        time.sleep(2)
        import shutil
        shutil.rmtree(temp_dir)
        print("[+] 临时文件已删除")

    except Exception as e:
        print(f"[!] 错误: {e}, {traceback.format_exc()}")


if __name__ == "__main__":

    pass

    # download_and_merge("我送亲人过大江.mp4", "https://www.dytt001.com/html/play/5021-1-23.html", proxy=False)

    # download_and_merge(
    #     "SPONE-001 【特典映像収録版】新人NO.1STYLE 国宝級清純フェイスに最強ボディとんでもない原石が遂に、脱ぐ 蒼井すずAVデビュー 蒼井鈴.mp4",
    #     input_url="https://adoda-smart-coin.mushroomtrack.com/hls/IzpKk3Fj_QbcQ3hgHIApJQ/1791971519/62000/62557/62557.m3u8",
    # )

    download_and_merge(
        "EBOD-699 極美肉感ボディ 究極グラマー愛人と理性吹き飛ぶ中出し不倫性交 しおりさん 神咲詩織.mp4",
        input_url="https://qabo-ahha.mushroomtrack.com/hls/WL9GtnS_eVYEZ5qOn-7BFw/1791971019/1000/1448/1448.m3u8",
    )



