"""
cctv视频的网址形如： https://v.cctv.com/2026/10/14/VIDEpSOtpm83zMdYUG7WmO11261014.shtml
其初始m3u8地址获取办法：
    开发者工具搜索：/asp/hls/main/ 在响应中会有形如如下的网址，其文件名为main.m3u8：
        https://newcntv.qcloudcdn.com/asp/hls/main/0303000a/3/default/9c0a7e67c2594c27a17e4c9021427ff8/main.m3u8?maxbr=2048
    可以用通用方法解析
    它的接口为：https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do？...

"""
import requests
import re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"


def get_guid(url):
    resp = requests.get(url, headers={
        "User-Agent": UA
    })
    pattern = r'var\s+guid\s*=\s*["\']([^"\']+)["\']\s*;?'
    match = re.search(pattern, resp.text)
    if match:
        return match.group(1)


def get_initial_m3u8_url(raw_url) -> dict:
    """
    调用央视官方API获取视频信息。
    """
    guid = get_guid(raw_url)
    api_url = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do"
    params = {"pid": guid}
    headers = {
        "User-Agent": UA,
        # "Referer": "https://v.cctv.com/",       # 可以不带
    }
    response = requests.get(api_url, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()['hls_url']


if __name__ == "__main__":
    url = "https://v.cctv.com/2026/09/30/VIDEXo9ea2euG3BX4Shim2rI260930.shtml"
    u = get_initial_m3u8_url(url)
    from common import get_final_m3u8_url
    uf = get_final_m3u8_url(u)
    print(uf)


    # print(u)
    # import m3u8
    # p = m3u8.load(u)
    # # print(p.__dict__)
    # from urllib.parse import urljoin
    # u2 = urljoin(p._base_uri, p.playlists[0].uri)
    # print(p._base_uri)
    # print(p.playlists[0].uri)
    # print(u2)
    # p2 = m3u8.load(u2)
    # # print(p2.__dict__)
