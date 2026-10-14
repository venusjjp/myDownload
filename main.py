import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import m3u8

from platforms import dytt, cctv
from platforms.common import check_ffmpeg, get_final_m3u8_url, parse_media_info, fetch_key, remove_advertisement, \
    merge_with_ffmpeg, download_ts

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
PROXIES = {
    "http": "127.0.0.1:7890",
    "https": "127.0.0.1:7890",
}
NO_PROXIES = {
    "http": None,
    "https": None,
}
WORKSPACE = ".\\workspace"
MAX_WORKERS = 10                    # 并发下载线程数
# DEFAULT_IV = b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'


def download_and_merge(
        output_file,
        input_url=None,          # 必备参数，即使提供本地m3u8，也需要从这个参数中获取base_url
        proxy=False,
        final_m3u8_url=None,       # 用于m3u8文件无法通过requests爬取时, 提供base_url
        final_m3u8_path=None,      # 用于m3u8文件无法通过requests爬取时, 从本地文件读取m3u8内容
        key_path=None,          # 用于加密key无法通过requests爬取时
):

    # 参数检查
    if input_url and final_m3u8_url:
        raise ValueError("不能同时提供 input_url 和 final_m3u8_url")
    if final_m3u8_url and not final_m3u8_path or not final_m3u8_url and final_m3u8_path:
        raise ValueError("final_m3u8_url 和 final_m3u8_path 必须同时提供")
    proxy = PROXIES if proxy else NO_PROXIES

    # 预处理
    os.makedirs(WORKSPACE, exist_ok=True)
    ffmpeg_path = check_ffmpeg()

    try:
        print(f"[*] 正在获取视频信息 ->《{output_file.removesuffix('.mp4')}》...")
        failure_list = []

        # 1. 获取 final m3u8 内容
        if input_url:
            if input_url.startswith("https://www.dytt001.com/"):        # 处理dytt网站
                initial_m3u8_url = dytt.get_initial_m3u8_url(input_url)
                print(f"[*] 获取到初始 m3u8 URL(dytt): {initial_m3u8_url}")
            elif 'cctv.com/' in input_url:
                initial_m3u8_url = cctv.get_initial_m3u8_url(input_url)
                print(f"[*] 获取到初始 m3u8 URL(cctv): {initial_m3u8_url}")
            else:
                initial_m3u8_url = input_url
                print(f"[*] 使用输入的 m3u8 URL: {initial_m3u8_url}")
            final_m3u8_url = get_final_m3u8_url(initial_m3u8_url)
            print(f"[*] 获取到final m3u8 url: {final_m3u8_url}")
            media_playlist = m3u8.load(final_m3u8_url)
        elif final_m3u8_path:
            with open(final_m3u8_path, 'r') as f:
                final_m3u8_content = f.read()
            media_playlist = m3u8.loads(final_m3u8_content)
        else:
            raise ValueError("必须提供 input_url 或 final_m3u8_url")

        media_info = parse_media_info(media_playlist)
        # 2. 处理加密
        if media_info["encrypted"]:
            if media_info['method'] != 'AES-128':
                raise RuntimeError(f'[!] 不支持此种加密方式: {media_info["method"]}')
            iv = media_info["iv"]
            key_url = media_info["key_url"]
            if key_path:
                with open(key_path, 'rb') as f:
                    key_bytes = f.read()
            else:
                key_bytes = fetch_key(key_url, headers=HEADERS, proxy=proxy)
            print(f'[*]该视频已加密，key: {key_bytes}, iv: {iv}')
        else:
            key_bytes = None
            iv = None
            print('[*]该视频未加密')

        # 3. 获取ts下载列表
        ts_list = media_info['ts_list']
        ts_list = remove_advertisement(ts_list)         # 删除广告片段

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
    #

    # download_and_merge(
    #     "cctv.mp4",
    #     input_url="https://v.cctv.com/2026/09/30/VIDEXo9ea2euG3BX4Shim2rI260930.shtml"
    # )

    download_and_merge(
        "JUR-856 昔私の事が好きだった粗チンの幼馴染が、性欲旺盛な巨チン男に進化していたので、何度も生ハメSEXに溺れてしまった…。 惠理",
        input_url="https://amuse-lefty.mushroomtrack.com/hls/Pa2uqHqpJhIkyphswUUzdw/1792006645/62000/62568/62568.m3u8"

    )
