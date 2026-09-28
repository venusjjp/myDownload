import os
import shutil
import re
import subprocess
import time
import urllib
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# -------------------------


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


def fetch_html(url, proxy=True):
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


def extract_index_url(html_text):
    pattern = r'https?:\\/\\/[^"\']+index\.m3u8'
    match = re.search(pattern, html_text)
    if match:
        cleaned = match.group(0).replace('\\/', '/')
        decoded = cleaned.encode('utf-8').decode('unicode_escape')
        encoded = urllib.parse.quote(decoded, safe=':/')
        return encoded
    else:
        raise ValueError("获取index url失败")


def get_base_url(url):
    return url.rsplit('/', 1)[0] + '/'


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


def decrypt_ts(cipher_bytes, key_bytes, iv=None):

    # key_bytes必须是bytes类型，长度为 16
    # IV 也必须是 bytes 类型，且长度必须为 16
    # 这里使用全 0 的 IV (标准默认值)
    # 此时 key 是 16 字节，iv 是 16 字节，符合 AES-128-CBC 标准
    if iv is None:
        iv = b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
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
                ts_bytes = decrypt_ts(resp.content, key_bytes, iv)
            else:
                ts_bytes = resp.content
            with open(save_path, "wb") as f:
                f.write(ts_bytes)
            return True
        except Exception as e:
            print(f"  [!] 尝试 {attempt+1}/{retries} 失败: {ts_url} - {e}")
    return False


def is_ad_segment(ts_url):
    """判断是否为广告片段（根据路径特征）"""
    # 根据你提供的 m3u8，广告片段都包含 "/video/adjump/"
    return "/video/adjump/" in ts_url


def identify_m3u8_type(m3u8_content):
    if "#EXT-X-STREAM-INF" in m3u8_content:
        return "MASTER"
    elif "#EXT-X-TARGETDURATION" in m3u8_content or "#EXTINF" in m3u8_content:
        return "MEDIA"


def parse_playlist_link(m3u8_content):
    lines = m3u8_content.splitlines()
    for line in lines:
        line = line.strip()
        if line.endswith(".m3u8"):
            return line


def parse_playlist_segments(m3u8_content):
    ts_list = []
    lines = m3u8_content.splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(".ts"):
            ts_list.append(line)
    return ts_list


def get_last_segments(ts_list, ts_base_url):
    ts_urls = []
    for ts in ts_list:
        if is_ad_segment(ts):
            print(f"  [-] 跳过广告片段: {ts}")
        else:
            ts_urls.append(urljoin(ts_base_url, ts))
    return ts_urls


def parse_m3u8(m3u8_url, proxy=True):

    m3u8_content = fetch_html(m3u8_url, proxy=proxy)
    # print(m3u8_content)
    result = identify_m3u8_type(m3u8_content)
    # print(result)
    if result == "MASTER":
        # 获取 mixed.m3u8 地址
        media_url = parse_playlist_link(m3u8_content)
        if media_url:
            print(f"  [+] 找到 media url: {media_url}")
        else:
            print("  [!] 未找到 次级m3u8")
            raise ValueError("未找到 次级m3u8")
    elif result == "MEDIA":
        media_url = m3u8_url
        """
        解析 MEDIA 类型的 M3U8 文件，返回所有非广告 .ts 片段的完整 URL 列表。
        """
    else:
        raise ValueError("未知的 M3U8 文件类型")
    ts_list = parse_playlist_segments(m3u8_content)
    ts_urls = get_last_segments(ts_list, get_base_url(media_url))
    return ts_urls


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
        output_file, dytt_url=None, m3u8_url=None, proxy=True,
        key_url=None, iv=None,
        key_bytes=None, m3u8_path=None):
    global PROXIES
    
    FFMPEG_PATH = check_ffmpeg()

    try:
        failure_list = []

        print(f"[*] 正在获取视频信息 ->《{output_file.removesuffix('.mp4')}》...")

        if dytt_url:
            html_text = fetch_html(dytt_url, proxy=proxy)
            # print(f"[*] 获取到html: {html_text}")
            index_url = extract_index_url(html_text)
            print(f"[*] 获取到index url: {index_url}")

            ts_urls = parse_m3u8(index_url, proxy=proxy)

            if not key_bytes:
                index_base_url = get_base_url(index_url)
                # 尝试获取key， 如果有值，则说明视频有加密
                index_key_url = index_base_url + "index.key"
                key_bytes = fetch_key(index_key_url, proxy=proxy)
                if key_bytes:
                    print(f"[*] 获取到加密用key: {key_bytes}")
                else:
                    print("[*] 该视频未加密")

        elif m3u8_url:
            if m3u8_path:
                with open(m3u8_path, "r", encoding="utf-8") as f:
                    m3u8_content = f.read()
                    ts_list = parse_playlist_segments(m3u8_content)
                    ts_urls = get_last_segments(ts_list, get_base_url(m3u8_url))
            else:
                ts_urls = parse_m3u8(m3u8_url, proxy=proxy)

        else:
            print("[!] 请提供 dytt_url 或 m3u8_url")
            raise ValueError("请提供 dytt_url 或 m3u8_url")

        if key_url:
            key_bytes = fetch_key(key_url, proxy=proxy)

        if not ts_urls:
            print("[!] 没有可下载的片段，退出。")
            return

        print(f"[*] 共发现 {len(ts_urls)} 个有效视频片段（已跳过广告）")

        # 3. 创建临时目录
        temp_dir = os.path.join(WORKSPACE, f'tmp_({output_file.removesuffix(".mp4")})')
        os.makedirs(temp_dir, exist_ok=True)

        # 4. 准备下载任务列表
        download_tasks = []
        for idx, url in enumerate(ts_urls):
            # 使用序号作为文件名，确保顺序正确
            filename = f"{idx:06d}.ts"
            save_path = os.path.join(temp_dir, filename)
            download_tasks.append((url, save_path, key_bytes, idx))

        # 5. 多线程下载
        print("[*] 开始下载片段（多线程）...")
        success_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_task = {
                executor.submit(download_ts, url, path, key_bytes): (url, path, key_bytes, idx)
                for url, path, key_bytes, idx in download_tasks
            }

            for future in as_completed(future_to_task):
                url, path, key_bytes, idx = future_to_task[future]
                try:
                    ok = future.result()
                    if ok:
                        success_count += 1
                        if success_count % 50 == 0:
                            print(f"  进度: {success_count}/{len(ts_urls)}")
                    else:
                        failure_list.append((path, url))
                        print(f"  [×] 下载失败: {url}")
                except Exception as e:
                    print(f"  [×] 异常: {url} - {e}")

        print(f"[*] 下载完成: 成功 {success_count} / 总数 {len(ts_urls)}")

        if success_count == 0:
            print("[!] 没有下载到任何片段，退出。")
            return
        elif success_count < len(ts_urls):
            print("[!] 以下片段下载失败：")
            for path, url in failure_list:
                print(f"  [×] 失败: {path} -> {url}")
            return

        # 6. 使用 ffmpeg 合并为 MP4
        merge_with_ffmpeg(temp_dir, output_file, ffmpeg_path=FFMPEG_PATH)

        # 7. （可选）清理临时文件
        print("合并完成，开始清除临时文件...")
        time.sleep(2)
        import shutil
        shutil.rmtree(temp_dir)
        print("[+] 临时文件已删除")

    except Exception as e:
        print(f"[!] 错误: {e}")


if __name__ == "__main__":

    pass

    download_and_merge("我送亲人过大江.mp4", "https://www.dytt001.com/html/play/5021-1-23.html", proxy=False)
    # download_and_merge("无双.mp4", "https://www.dytt001.com/html/play/15678-1-1.html", proxy=False)
    # download_and_merge("未麻的部屋.mp4", "https://www.dytt001.com/html/play/57729-1-1.html", proxy=False)


    # for i in [52, ]:
    #     url = f'https://www.dytt001.com/html/play/7281-1-{i}.html'
    #     name = f'射雕英雄传_{i}.mp4'
    #     print(f'开始下载：{name}')
    #     download_and_merge(name, dytt_url=url, proxy=False)



